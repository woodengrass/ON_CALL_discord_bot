# ADR-0001: Track L RPC 傳輸協定與訊息格式

**Status:** Proposed
**Date:** 2026-07-13（v2：依複查意見修正，見文末變更記錄）
**Deciders:** woodengrass（專案擁有者）

## Context

`discord_plugin_platform/design.md` Track L 已拍板產品目標：伺服器使用者只邀請、只看見一個 Discord Bot。主 bot（`honeypot-discord-bot`）是唯一 Discord Application 身分、唯一持有 Token、唯一能呼叫 Discord API 的行程；外掛平台維持獨立 Python 服務與獨立 Lua 子行程，兩者永遠不共用行程（兩專案都有頂層 `core/` 套件，同行程 import 會衝突，這是硬約束，不是風格選擇）。

design.md Track L.2 明確要求「實作前必先寫 ADR」，並列出這份 ADR 必須定案的項目：

- transport 必須同時支援 Windows 與 Linux（開發機是 Windows，README 同時提供 Linux 部署說明，兩者都要能跑，不能有僅單一平台可用的 fallback）、只接受本機連線、具備服務身分驗證與每次啟動可 rotation 的 secret。
- message kind 採 allowlist；未知 kind、版本、欄位與超限 payload 一律拒絕並記錄。
- 支援 deadline、cancel、雙方各自重啟、連線中斷、重試與 idempotency；任一端失敗不得讓 Discord event loop 永久卡住。
- 不得序列化 token、資料庫連線、Discord object、任意 Python object 或 callable，payload 只能是明確 schema 的 primitive JSON-like 資料。
- 需要端到端 trace：同一個 Discord event 要能從主 bot event、RPC request、sandbox child、capability request、action validation 一路追到 Discord API 結果。

另外 Track L.1 定義了雙軌同步模型，這份 ADR 只處理其中**高頻、需回應**的那一半（審核／退回／封鎖／停權／方案這類低頻狀態變更已經是既有的「寫資料庫＋輪詢」模式，不在這份 ADR 範圍內）：

1. 主 bot 送入 Discord 事件 → 平台執行外掛。
2. 平台在執行外掛期間，需要**回頭**向主 bot 查詢能力資料（例如 `get_member_role_ids`）——這是這份 ADR 要處理的核心難點：外層請求（事件分派）還沒結束，中間需要一次反向呼叫。
3. 平台把外掛產生的 action 清單回傳給主 bot。
4. 主 bot 驗證並實際執行 action（呼叫真正的 Discord API）。

**這份 ADR v1 經過一輪獨立複查，抓到 3 個 High、4 個 Medium、1 個 Low 的具體缺口**（授權範圍、cancel 語意、idempotency 語意、Pydantic 行為誤判、payload 上限實際生效位置、密鑰輪替生命週期、套件分發計畫、文件狀態矛盾）。v2（本版）逐項修正，複查意見原文與對應章節對照見文末〈複查回應記錄〉。**這份 ADR 修正前不得視為 gate 已通過**，design.md 不應標記 L.5 step 1 完成，直到使用者確認接受這一版。

## Decision

**兩個各自獨立的本機 HTTP 服務**，走 loopback TCP（`127.0.0.1`，固定預設 port、可用環境變數覆蓋），JSON body 一律用一個新的、不 import 任一方 `core` 的獨立套件 `rpc_protocol/` 裡定義的 Pydantic schema 驗證：

- **主 bot 開一個小型 HTTP 伺服器**（用 `aiohttp.web`——主 bot 已經依賴 `aiohttp`，不用新增套件），對外只暴露「能力查詢」端點，供平台在執行外掛期間回呼查詢 `get_member_role_ids` 這類資料。主 bot 同時是**呼叫方**，用 `aiohttp.ClientSession` 把 Discord 事件送給平台。
- **平台開另一個小型 HTTP 伺服器**（新開一個 FastAPI app，跟 `web/admin` 的 app 分開、不同 port——平台已經依賴 `fastapi`/`uvicorn`/`pydantic`，符合既有慣例），對外暴露「事件分派」端點，接收主 bot 送來的事件、執行外掛、需要能力資料時回呼主 bot 的能力端點、最後把 action 清單當作這次 HTTP 回應回傳。平台同時是能力查詢的**呼叫方**，用 `httpx`（新增一個輕量 async client 依賴）呼叫主 bot 的能力端點。
- **每則訊息**都帶 `request_id`、`correlation_id`（同一個 Discord event 從頭到尾共用同一個 `correlation_id`）、`protocol_version`、絕對 UTC `deadline`。

以下五個子章節是 v2 新增／改寫的核心機制，直接對應複查抓到的 High/Medium 缺口。

### D.1 授權模型：capability lease，不是只驗證「你是平台」（回應複查 #1）

v1 的共用密鑰只證明「這個呼叫方是平台行程」，沒有限制「這次呼叫只能查跟當次執行有關的資料」。平台如果有 bug 或被入侵，可以用同一把密鑰查任何 guild／plugin 的能力資料，不受限於當下真的在執行哪個外掛。

**修正**：主 bot 分派事件給平台時，額外簽發一個**短效、不可猜測的 execution lease**（隨機 token，例如 32 bytes 的 `secrets.token_urlsafe`），跟這次 dispatch 綁定，內容涵蓋：

```json
{
  "lease_id": "opaque random token",
  "correlation_id": "...",
  "guild_id": 123,
  "installation_id": "...",
  "plugin_id": "...",
  "plugin_version": "...",
  "allowed_capabilities": ["get_member_role_ids", "..."],
  "expires_at": "2026-07-13T12:00:03Z",
  "max_calls": 20
}
```

- lease 隨 `DispatchEventRequest` 一起交給平台。平台每次呼叫主 bot 的能力端點都必須附上這個 lease（放在 body，不是只放共用密鑰）。
- 主 bot 收到能力查詢時，**共用密鑰只證明「這是平台在說話」，實際授權完全看 lease**：驗證 lease 存在、未過期、`correlation_id`／`guild_id`／`installation_id`／`plugin_id`／`plugin_version` 跟這次查詢的 request body 完全相符、要查的能力函式在 `allowed_capabilities` 白名單內、還沒超過 `max_calls`。任一項不符直接拒絕（403），記 log（含 `lease_id`、`correlation_id`，不含密鑰本身）。
- lease 只在主 bot 記憶體裡追蹤（不用寫資料庫——這是單次事件執行期間的短命物件，事件處理完或逾時就失效，沒有跨行程持久化的必要）。
- 主 bot 重啟＝所有未過期 lease 全部失效（記憶體結構本來就沒了），平台這時任何帶著舊 lease 的呼叫都會被拒絕，這是預期行為，不是 bug——見 D.4。

### D.2 Deadline 與 cancel：定義「真的停止」的語意，不是只有呼叫方放棄等待（回應複查 #2）

v1 寫「deadline 用 HTTP client timeout、cancel 用連線中斷偵測」——這只保證**呼叫方不再等待**，不保證**接收方真的停止工作**。HTTP client timeout 純粹是本地行為；連線中斷偵測也只在某一種斷線情境下才會觸發，且不會自動往下傳到「正在跑的 Lua 子行程」這一層。

**修正，拆成三層明確定義**：

1. **Deadline 是絕對 UTC 時間，不是相對秒數**：訊息 envelope 裡的 `deadline` 欄位是 ISO 8601 UTC timestamp，不是「還剩幾秒」。理由：一個請求可能經過多次轉發（主 bot → 平台 → 平台回呼主 bot 查能力），相對秒數每經一手就要重新計算、容易算錯；絕對時間戳只要比對「現在」跟「deadline」就好，所有經手方算出來的結果一致。**任何一端在開始處理請求前，都要先檢查 `now() < deadline`，已過期直接回 408，不執行任何工作**（包含不 spawn 子行程、不呼叫下一層 RPC）。
2. **明確的 cancel message，用 `request_id` 定位**：新增一個 `POST /cancel` 端點（兩邊都要有），body 是 `{"request_id": "...", "reason": "..."}`。呼叫方判定「不再需要這個結果」時（例如自己的上層也被取消了、或使用者手動介入）主動呼叫對方的 `/cancel`，不是只是斷線了事。
3. **cancel 必須真的往下傳到子行程層**：平台收到 cancel（或自己的 deadline 到期）時，若這個 `request_id` 對應的外掛執行已經 spawn 了 Lua 子行程（Track G 的沙箱子行程本來就有逾時 terminate/kill 機制），要主動呼叫既有的終止路徑（`terminate()` → 逾時再 `kill()` → `join()`回收），不是放著讓它自然跑完。**cancel 生效後，這次執行產生的任何 action 都不可以再被主 bot 執行**——即使子行程剛好在被殺之前已經把 action 清單送出來了，主 bot 收到「這個 `request_id` 已 cancel」的狀態時要直接丟棄，不執行。

### D.3 Idempotency 與重試：按訊息類型分別定義，不是一律套用同一個 key（回應複查 #3）

v1 只說「造成狀態變更的訊息帶 idempotency key」，但沒定義三種訊息各自的重試/去重責任方，也誤把 `ActionExecutionReport`（回報結果，附帶效應）當成主要副作用來源——**真正有副作用的是主 bot 執行 action 本身（呼叫 Discord API），不是事後的回報**。

**修正，逐類定義**：

| 訊息類型 | 誰可能重試 | 誰負責去重 | 去重依據與保留時間 | 可重試的 HTTP status | 最大重試次數 |
|---|---|---|---|---|---|
| `DispatchEventRequest`（主 bot → 平台，分派事件） | 主 bot（例如逾時不確定平台有沒有收到） | 平台 | 以 `request_id` 為 key，平台維護 in-flight／completed 兩種狀態；收到重複 `request_id` 且已有結果，直接回同一份結果，不重新執行外掛；in-flight 中收到重複視為併發保護（回 409），不是靜默忽略 | 連線失敗（無回應）、504 | 2 次，間隔遞增 |
| `CapabilityLookupRequest`（平台 → 主 bot，查能力資料） | 平台 | 主 bot（但這類請求本質是唯讀查詢，冪等性天然成立，不需要額外 dedupe 表，重試安全） | 不需要持久化 dedupe，唯讀查詢重複執行結果一致 | 連線失敗、504、429（見 D.5 過載保護） | 3 次 |
| **Action 執行**（主 bot 實際呼叫 Discord API 的動作，`ActionExecutionReport` 只是這件事的回報） | 平台（回傳 action 清單給主 bot 後，若沒收到主 bot 的執行結果） | **主 bot**——這是唯一真正有外部副作用（真的發訊息、真的加身分組）的一類，去重責任必須在真正呼叫 Discord API 的那一端 | 每個 action 有獨立 `action_idempotency_key`（平台產生，跟 action 內容一起送）。主 bot 收到 action 執行請求時，先查這個 key 是否已執行過（有界 TTL 記憶體快取即可，比照現有 quota/suspension 這類「記憶體狀態＋有限時窗」的既有模式，不需要新的持久化資料表——action 執行是即時性操作，不需要跨行程重啟存活）；已執行過就直接回上次的結果，不重複呼叫 Discord API | 連線失敗、504 | 1 次（action 執行不安全重試超過一次，避免鍵值碰撞風險放大；失敗就回報給平台，不在主 bot 內部自動重試多次） |

### D.4 密鑰輪替的完整生命週期（回應複查 #6）

v1「讀不到或不是最新就重試」沒有定義「最新」怎麼判斷。**修正**：

- 密鑰檔案內容不只是密鑰本身，還要有 **generation 編號**（單調遞增整數）：`{"generation": 3, "secret": "...", "issued_at": "..."}`。
- 主 bot 每次啟動：generation 從上次的值 +1（讀取舊檔案取得上次 generation，讀不到就從 1 開始），產生新密鑰，**先寫進暫存檔、再原子 rename 覆蓋正式檔**（避免平台讀到寫一半的內容），檔案權限設為僅擁有者可讀寫。
- 平台端維護目前用的 generation；每次呼叫主 bot 若收到 401（密鑰不符），**先重新讀一次密鑰檔案，generation 比自己手上的新就更新並重試一次這次呼叫；重試後還是 401 才視為真正的設定錯誤，記錄 ERROR log 並依 D.5 的過載/失敗政策處理**，不是無限重試。
- 主 bot 重啟＝新 generation＝舊 generation 簽發的所有 lease（D.1）全部失效，平台此時任何用舊 lease 發出的呼叫會被拒（因為 lease 只存在記憶體，重啟後也一起消失），平台應該把「主 bot 重啟」視為所有進行中的 in-flight dispatch 都可能已經遺失結果，比照 D.3 的重試表處理。
- **密鑰絕對不得出現在**：log 訊息、例外訊息（catch 到驗證失敗時只記 generation 與呼叫來源，不記密鑰內容）、process 啟動參數（`sys.argv` 在同機器上其他使用者可能看得到，密鑰只能透過檔案或環境變數傳遞，不能當 CLI 參數）、`/health` 端點回應。

### D.5 過載與失敗政策（D.3 表格提到的「依過載政策處理」在此定義）

- 任一端收到超過自己能力上限的請求量時，回 429（Too Many Requests），body 帶 `retry_after_ms`。呼叫方遵守 `retry_after_ms` 再重試，不是立刻無腦重試造成雪崩。
- 這只定義「RPC 層的過載回應格式」，實際的 admission control 數字（全平台最大 sandbox children、每 guild event queue 上限等）屬於 design.md L.4，不在這份 ADR 範圍內，L.5 step 6 才處理。

### D.6 Schema 嚴格性：修正一個事實錯誤（回應複查 #4）

v1 在 Option A 的 Pros 裡寫「Pydantic 本來就會拒絕未知欄位／型別不符」——**這是錯的**。Pydantic v2 預設會**忽略**多餘欄位，也會做寬鬆的型別轉換（例如字串 `"123"` 自動轉成 `int`），兩者都違反 Track L.2「未知欄位／版本／欄位一律拒絕」的硬性要求。

**修正**：`rpc_protocol` 裡**每一個** model（包含所有巢狀 model）都必須明確設定：

```python
model_config = ConfigDict(extra="forbid", strict=True)
```

`rpc_protocol` 的契約測試必須包含：帶多餘欄位的 payload 要被拒絕、型別不符（例如該傳 int 卻傳字串）要被拒絕、巢狀 model 也要驗證同樣的規則（不能只測最外層）。

### D.7 Payload 上限要在 HTTP 層強制，不能只讓 Pydantic 事後把關（回應複查 #5）

Pydantic 是「body 已經被讀進記憶體、解析成 JSON 之後」才驗證，這時超大 payload 造成的記憶體壓力已經發生了，Pydantic 再拒絕也於事無補（尤其如果是刻意的超大 payload 攻擊，或單純上游 bug 造成的無限迴圈資料）。

**修正**：

- `aiohttp.web.Application` 建立時設定 `client_max_size`（明確位元組數，例如 256 KiB——這個協定只傳事件 payload／action 清單／能力查詢結果，不傳檔案，256 KiB 給足夠餘裕）。
- FastAPI（platform 端）用 ASGI middleware 在 body 讀取階段就檢查 `Content-Length`，超過上限直接回 413，不讀完整個 body 才驗證。
- `rpc_protocol` 的 schema 除了整體 payload 上限，個別欄位也要有上限（字串長度、陣列元素數量）——例如 action 清單的陣列長度上限、單一 action 的 payload dict 大小上限，避免「整體 payload 沒超標，但單一欄位塞爆」的繞過方式。
- 契約測試要包含：超過整體上限的 body 被 413 拒絕（不進入 Pydantic 驗證階段）、單一欄位超限但整體不超限的 payload 被 422 拒絕。

## Options Considered

### Option A：本機 HTTP（JSON + Pydantic，`extra="forbid"`）—— 採用

| Dimension | Assessment |
|---|---|
| 複雜度 | 低-中：直接用兩專案現有的框架（主 bot 已有 `aiohttp`，平台已有 `fastapi`），不用學新工具 |
| 跨平台 | 佳：TCP 在 Windows／Linux 行為一致，asyncio 對兩邊都是一級支援 |
| 成本 | 低：新增依賴是平台端的 `httpx`，以及主 bot 端新增 `pydantic`（透過 `rpc_protocol` 套件間接引入，見 D.8） |
| 團隊熟悉度 | 高：兩個框架都已經是這個 repo 既有的程式碼風格 |
| deadline/cancel 支援 | 需要明確設計（見 D.2），HTTP 本身不會無償提供，但可以在這個 transport 上完整實作 |
| 除錯難度 | 低：可以直接用 `curl` 重現任何一次呼叫，JSON 人眼可讀 |

**Pros：** 重用既有依賴、跨平台零額外處理、除錯友善、schema 驗證可以完整對應 L.2 的 allowlist 要求（**但必須明確設定 `extra="forbid"`／`strict=True`，不是預設行為，見 D.6**）、兩邊測試模式（TestClient／假 server）都已經在這個 repo 出現過。
**Cons：** 需要兩個獨立 server（不是單一長連線），「平台執行中回呼主 bot」這個巢狀模式需要小心設計避免死鎖（見下方風險）；deadline/cancel/idempotency 語意都要自己明確定義（見 D.2、D.3），HTTP 協定本身不會無償提供這些保證；HTTP/JSON 比二進位協定多一點開銷，但這個規模（單一小型 bot、每秒最多幾個事件）完全不是問題。

### Option B：Unix Domain Socket

| Dimension | Assessment |
|---|---|
| 複雜度 | 中 |
| 跨平台 | **不通過**：Python `asyncio` 在 Windows 上不支援 `create_unix_server`（`NotImplementedError`），雖然 Windows 10 1803 之後作業系統本身有 `AF_UNIX`，但 asyncio 沒接上，等於要幫 Windows 另外做一套 fallback transport |

**Pros：** Linux 上可以用檔案權限做存取控制，不用密鑰。
**Cons：** 直接違反 L.2「必須同時支援 Windows 與 Linux」的硬性要求，而且開發機就是 Windows——為了 Linux 一半的路徑省一個密鑰機制，換來要多維護一套 Windows-only transport，不划算。

### Option C：gRPC（protobuf）over loopback TCP

| Dimension | Assessment |
|---|---|
| 複雜度 | 高：新的 codegen 工具鏈、`.proto` schema 是額外的一份真相來源、`grpcio` 是體積不小的二進位依賴 |
| 跨平台 | 佳 |
| deadline/cancel/雙向串流 | 協定本身有內建機制，但仍需要應用層定義 D.1 的授權範圍與 D.3 的業務層 idempotency——這兩件事不是 transport 能免費提供的，換 gRPC 不會讓 D.1/D.3 的工作量消失 |

**Pros：** deadline／cancel／雙向串流是協定本身就有的一級功能，型別產生的 client 很嚴謹。
**Cons：** 依賴最重；`.proto` schema 跟 Python 型別是兩份要手動保持同步的東西，跟這個專案 `web/admin` 已經在用的 Pydantic 慣例不一致；對「兩個 Python 行程、同一台機器、事件量很小的 Discord bot」這個規模來說是過度設計，學習成本也最高（單人維護）；且 D.1 的 lease 授權範圍、D.3 的業務層 idempotency 語意都還是要自己設計，gRPC 只解決 transport 層的 deadline/cancel，不解決這份 ADR真正困難的部分。

### Option D：Windows 具名管道 + Linux Unix Socket（兩套實作）

| Dimension | Assessment |
|---|---|
| 複雜度 | 高：兩條路徑，永遠要一起維護、一起測 |

**Pros：** 各平台最「原生」的 IPC 方式，省一個 TCP port。
**Cons：** 換來的效能差異在這個規模完全無感，卻要雙倍實作與測試成本，違反這個專案自己的「不要過度設計」慣例。

## Trade-off Analysis

- **雙向的核心矛盾其實不需要雙向連線來解**：L.1.2 要求的「平台執行外掛途中要回頭問主 bot 能力資料」看起來像是需要一條雙向長連線（WebSocket／gRPC streaming），但拆開看，「主 bot 分派事件給平台」跟「平台向主 bot 查能力資料」是兩個**方向相反、彼此獨立**的請求/回應關係。讓兩邊各自開一個小 server、互相當對方的 client，比維護一條共享雙向連線的狀態機更簡單、更容易各自測試、也更符合「兩邊要能各自重啟」這個 Track L 明確的驗收條件——重啟其中一邊，另一邊只是暫時連不上（HTTP client 重試/報錯），不會有連線狀態要復原的問題。
- **Windows 相容性直接淘汰 Option B**：開發機是 Windows，README 也承諾 Linux 部署，兩邊行為必須一致，不能有平台限定路徑。
- **這個規模不需要 gRPC 的效能特性，而且 gRPC 不會替你解決真正困難的部分**：單一小型 Discord bot，事件量遠遠稱不上需要 gRPC 解決的問題；更關鍵的是，這份 ADR 真正的難點是 D.1（授權範圍）跟 D.3（業務層 idempotency），這兩個不管換哪種 transport 都要自己設計，gRPC 換不到這部分的免費午餐，只換到更重的依賴。
- **重用既有依賴把新增依賴壓到最低**：主 bot 端新增 `pydantic`（透過 `rpc_protocol`）與內建的 `aiohttp` server 端功能，平台端只多一個 `httpx`。比起導入一整套新協定框架，維護成本低很多，也符合單人維護的現實。
- **Pydantic schema 是 L.2「message kind allowlist」要求最直接的實作方式——前提是設對 `ConfigDict`**：一份 schema、兩邊共用（透過新的 `rpc_protocol` 套件），不會出現「主 bot 這樣驗、平台那樣驗」的行為落差——這正是 H.0 共用操作層當初要解決的同一類風險，這裡用同樣的邏輯處理跨行程協定。

## Consequences

- **變簡單的事**：兩邊可以獨立重啟不掉連線狀態（每個請求都是無狀態 HTTP）；測試不需要真的 Discord 連線或真的沙箱子行程，`rpc_protocol` 的 schema 可以獨立寫契約測試；除錯可以直接用 `curl` 重放任何一次呼叫；兩邊工程師/agent 上手快，因為框架都已經在這個 repo 出現過。
- **變困難的事**：
  - 「平台在處理主 bot 送來的事件時，途中回呼主 bot 查能力資料」這個巢狀呼叫模式，實作時必須小心避免死鎖——例如主 bot 處理能力查詢的 handler 不能持有任何平台端 dispatch handler 也需要的鎖／資源。
  - D.1 的 lease 機制、D.2 的 cancel 往下傳到子行程、D.3 的逐類型 idempotency，都是需要謹慎實作與測試的正確性關鍵路徑，不是「converted to HTTP 就自動解決」的事。
  - 這些都列進下方 Action Items，是實作階段的明確工作項，不是這份 ADR 能一次寫完就結束的。
- **未來要重新評估的事**：目前假設同一台機器只跑一個 bot 實例，所以 port 用固定值＋環境變數覆蓋就夠；如果之後同一台主機要跑多個 bot 實例，固定 port 會撞號，需要動態配置——現在只需要確保「port 從一開始就是環境變數可覆蓋」，不用現在就解決動態配置問題。
- **新增的維運複雜度**：`rpc_protocol` 成為第三個「要保持同步」的東西（主 bot、平台、協定套件三方）。用 `PROTOCOL_VERSION` 常數＋每個請求都檢查版本、版本不符直接拒絕，防止兩邊悄悄長歪而不自知。

## D.8 `rpc_protocol` 套件分發計畫（回應複查 #7）

v1 沒定義 `rpc_protocol` 實際上怎麼被兩邊 import——只說「新建一個獨立套件」，沒說它是 workspace package、wheel 還是「剛好在同一個 repo 裡所以 import 得到」。後者是最脆弱的做法（依賴兩邊工作目錄的相對位置恰好正確），必須明確排除。

**修正**：

- `rpc_protocol/` 在 repo 根目錄，**有自己完整的 `pyproject.toml`**（獨立套件，`name = "rpc-protocol"`，依賴只有 `pydantic`），比照 `discord_plugin_platform/` 現有的獨立套件模式。
- 主 bot（`honeypot-discord-bot/pyproject.toml`）與平台（`discord_plugin_platform/pyproject.toml`）都在各自的 `dependencies` 加一行**本機路徑依賴**：`"rpc-protocol @ file:///${PROJECT_ROOT}/../rpc_protocol"`（實際寫法在實作時依 pip 當時版本支援的語法定案，原則是明確的可安裝依賴，不是 `sys.path` 魔術）。兩邊各自 `pip install -e .` 時會一併裝好同一份 `rpc_protocol`，不會出現「兩邊裝到不同版本」的分裂。
- 主 bot 目前的 `pyproject.toml` 沒有 `pydantic`，這個依賴會透過 `rpc_protocol` 間接引入——這是預期且必要的新增依賴，不是意外的相依污染，寫進這份 ADR 讓这個新增依賴的來源有明確記錄。
- 版本策略：`rpc_protocol` 自己的套件版本號與協定內的 `PROTOCOL_VERSION` 常數綁在一起遞增（套件版本變了，`PROTOCOL_VERSION` 就要跟著變），兩邊執行期都檢查對方回報的 `protocol_version` 是否跟自己一致，不一致直接拒絕並記錄 ERROR（比對版本不吻合，代表兩邊裝的 `rpc_protocol` 沒有同步升級，這是設定錯誤，不該讓請求靜默用不相容的方式被處理）。

## Action Items

1. [ ] 建立獨立套件 `rpc_protocol/`（repo 根目錄，自己的 `pyproject.toml`，見 D.8）：定義每種訊息的 Pydantic schema（`DispatchEventRequest/Response`、`CapabilityLookupRequest/Response`（含 D.1 的 lease 欄位）、`ActionExecutionRequest/Report`（含 D.3 的 `action_idempotency_key`）、`CancelRequest`（D.2）、`HealthCheck`），所有 model（含巢狀）都設 `ConfigDict(extra="forbid", strict=True)`（D.6），`PROTOCOL_VERSION` 常數，共用密鑰檔案讀寫工具（含 D.4 的 generation 欄位與原子寫入）。
2. [ ] `rpc_protocol` 契約測試：D.6 的未知欄位/型別不符拒絕測試（含巢狀 model）、D.7 的欄位級/整體 payload 上限測試、D.1 的 lease 欄位驗證測試。整個套件不依賴主 bot／平台任何其他程式碼即可獨立跑測試。
3. [ ] 主 bot：新增 `aiohttp.web` 伺服器，只綁 `127.0.0.1`，設定 `client_max_size`（D.7），暴露能力查詢端點（驗證 D.1 的 lease）與 `/cancel` 端點（D.2）；維護 action 執行的 idempotency 快取（D.3）；新增對平台事件分派端點的 `aiohttp.ClientSession` 呼叫，含 D.4 的密鑰 reload-on-401 邏輯。
4. [ ] 平台：新增獨立 FastAPI app（跟 `web/admin` 分開，不同 port），設定 body 大小限制 middleware（D.7），暴露事件分派端點（簽發並附帶 D.1 的 lease）與 `/cancel` 端點；新增 `httpx` 依賴，實作對主 bot 能力端點的呼叫，含 D.3 的 in-flight/completed dedupe 狀態。
5. [ ] 密鑰輪替：依 D.4 完整生命週期實作（generation 編號、原子寫入、僅擁有者可讀權限、平台端 reload-on-401、絕不寫入 log／例外／CLI 參數／health 回應）。
6. [ ] `correlation_id` 從 Discord 事件進入主 bot 的第一刻產生，貫穿之後每一次 RPC 呼叫（含 lease、cancel、dedupe 相關的所有 log 行）。
7. [ ] 巢狀回呼死鎖風險：實作 L.5 step 3-4（真的接上 capability request/action request）時，明確設計主 bot 能力查詢 handler 的並行模型（例如每個查詢在獨立 task 處理，不共用會被 dispatch handler 卡住的鎖／連線）；cancel（D.2）要真的能中止已 spawn 的 Track G 沙箱子行程，不是只標記狀態。
8. [ ] 這份 ADR 經使用者確認接受、狀態改為 `Accepted` 後，才滿足 design.md L.5 step 1；下一步是 L.5 step 2（把 `rpc_protocol` 套件與雙端契約測試建好，不接真實 Discord）。

## 複查回應記錄（v1 → v2）

| # | 嚴重度 | 複查意見摘要 | 本版對應章節 |
|---|---|---|---|
| 1 | High | Bearer secret 只驗證平台身分，沒把能力查詢限制到當次 event | D.1（execution lease） |
| 2 | High | deadline/cancel 沒有真正定義可中止語意 | D.2 |
| 3 | High | retry/idempotency 語意不足，可能重複執行 Discord action | D.3 |
| 4 | Medium | 「Pydantic 會拒絕未知欄位」是錯誤假設 | D.6 |
| 5 | Medium | payload 上限未定義 HTTP server 層的強制位置 | D.7 |
| 6 | Medium | secret rotation 的啟動/重啟流程不完整 | D.4 |
| 7 | Medium | protocol package 的安裝/依賴計畫缺失 | D.8 |
| 8 | Low | ADR 是 Proposed，但 design.md 已標示完成 | 見文首狀態聲明；design.md 已同步修正，不再標記完成 |
