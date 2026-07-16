# ADR-0001: Track L RPC 傳輸協定與訊息格式

**Status:** Proposed
**Date:** 2026-07-13
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

## Decision

**兩個各自獨立的本機 HTTP 服務**，走 loopback TCP（`127.0.0.1`，固定預設 port、可用環境變數覆蓋），JSON body 一律用一個新的、不 import 任一方 `core` 的獨立套件 `rpc_protocol/` 裡定義的 Pydantic schema 驗證：

- **主 bot 開一個小型 HTTP 伺服器**（用 `aiohttp.web`——主 bot 已經依賴 `aiohttp`，不用新增套件），對外只暴露「能力查詢」端點，供平台在執行外掛期間回呼查詢 `get_member_role_ids` 這類資料。主 bot 同時是**呼叫方**，用 `aiohttp.ClientSession` 把 Discord 事件送給平台。
- **平台開另一個小型 HTTP 伺服器**（新開一個 FastAPI app，跟 `web/admin` 的 app 分開、不同 port——平台已經依賴 `fastapi`/`uvicorn`/`pydantic`，符合既有慣例），對外暴露「事件分派」端點，接收主 bot 送來的事件、執行外掛、需要能力資料時回呼主 bot 的能力端點、最後把 action 清單當作這次 HTTP 回應回傳。平台同時是能力查詢的**呼叫方**，用 `httpx`（新增一個輕量 async client 依賴）呼叫主 bot 的能力端點。
- **共用密鑰**：主 bot 每次啟動時產生一組亂數密鑰，寫進一個權限受限（僅擁有者可讀）的本機檔案；平台啟動時讀取同一份檔案（讀不到或密鑰不是最新的就重試，不猜測、不硬編碼）。每個 RPC 請求都帶著這把密鑰（`Authorization: Bearer <secret>`），任一端驗證失敗就拒絕並記 log。
- **每則訊息**都帶 `request_id`、`correlation_id`（同一個 Discord event 從頭到尾共用同一個 `correlation_id`，方便串起整條 trace）、`protocol_version`、`deadline`。deadline 用 HTTP client 逾時實作；cancel 用連線中斷偵測（ASGI/aiohttp 都原生支援偵測對端斷線）。
- 會造成狀態變更的訊息（例如 action 執行結果回報）額外帶 `idempotency_key`，讓重試不會重複執行。

## Options Considered

### Option A：本機 HTTP（JSON + Pydantic）—— 採用

| Dimension | Assessment |
|---|---|
| 複雜度 | 低-中：直接用兩專案現有的框架（主 bot 已有 `aiohttp`，平台已有 `fastapi`），不用學新工具 |
| 跨平台 | 佳：TCP 在 Windows／Linux 行為一致，asyncio 對兩邊都是一級支援 |
| 成本 | 低：新增依賴只有平台端的 `httpx`（輕量） |
| 團隊熟悉度 | 高：兩個框架都已經是這個 repo 既有的程式碼風格 |
| deadline/cancel 支援 | 原生：HTTP client timeout + 連線中斷偵測 |
| 除錯難度 | 低：可以直接用 `curl` 重現任何一次呼叫，JSON 人眼可讀 |

**Pros：** 重用既有依賴、跨平台零額外處理、除錯友善、schema 驗證直接對應 L.2 的 allowlist 要求（Pydantic 本來就會拒絕未知欄位／型別不符，不用手刻驗證邏輯）、兩邊測試模式（TestClient／假 server）都已經在這個 repo 出現過。
**Cons：** 需要兩個獨立 server（不是單一長連線），「平台執行中回呼主 bot」這個巢狀模式需要小心設計避免死鎖（見下方風險）；HTTP/JSON 比二進位協定多一點開銷，但這個規模（單一小型 bot、每秒最多幾個事件）完全不是問題。

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
| deadline/cancel/雙向串流 | 原生內建，是這個協定的強項 |

**Pros：** deadline／cancel／雙向串流是協定本身就有的一級功能，型別產生的 client 很嚴謹。
**Cons：** 依賴最重；`.proto` schema 跟 Python 型別是兩份要手動保持同步的東西，跟這個專案 `web/admin` 已經在用的 Pydantic 慣例不一致；對「兩個 Python 行程、同一台機器、事件量很小的 Discord bot」這個規模來說是過度設計，學習成本也最高（單人維護）。

### Option D：Windows 具名管道 + Linux Unix Socket（兩套實作）

| Dimension | Assessment |
|---|---|
| 複雜度 | 高：兩條路徑，永遠要一起維護、一起測 |

**Pros：** 各平台最「原生」的 IPC 方式，省一個 TCP port。
**Cons：** 換來的效能差異在這個規模完全無感，卻要雙倍實作與測試成本，違反這個專案自己的「不要過度設計」慣例。

## Trade-off Analysis

- **雙向的核心矛盾其實不需要雙向連線來解**：L.1.2 要求的「平台執行外掛途中要回頭問主 bot 能力資料」看起來像是需要一條雙向長連線（WebSocket／gRPC streaming），但拆開看，「主 bot 分派事件給平台」跟「平台向主 bot 查能力資料」是兩個**方向相反、彼此獨立**的請求/回應關係。讓兩邊各自開一個小 server、互相當對方的 client，比維護一條共享雙向連線的狀態機更簡單、更容易各自測試、也更符合「兩邊要能各自重啟」這個 Track L 明確的驗收條件——重啟其中一邊，另一邊只是暫時連不上（HTTP client 重試/報錯），不會有連線狀態要復原的問題。
- **Windows 相容性直接淘汰 Option B**：開發機是 Windows，README 也承諾 Linux 部署，兩邊行為必須一致，不能有平台限定路徑。
- **這個規模不需要 gRPC 的效能特性**：單一小型 Discord bot，事件量遠遠稱不上需要 gRPC 解決的問題，它的複雜度成本沒有對應的真實需求可以攤提。
- **重用既有依賴把新增依賴壓到最低**：主 bot 端幾乎零新依賴（`aiohttp` 已經在用），平台端只多一個 `httpx`。比起導入一整套新協定框架，維護成本低很多，也符合單人維護的現實。
- **Pydantic schema 是 L.2「message kind allowlist」要求最直接的實作方式**：一份 schema、兩邊共用（透過新的 `rpc_protocol` 套件），不會出現「主 bot 這樣驗、平台那樣驗」的行為落差——這正是 H.0 共用操作層當初要解決的同一類風險，這裡用同樣的邏輯處理跨行程協定。

## Consequences

- **變簡單的事**：兩邊可以獨立重啟不掉連線狀態（每個請求都是無狀態 HTTP）；測試不需要真的 Discord 連線或真的沙箱子行程，`rpc_protocol` 的 schema 可以獨立寫契約測試；除錯可以直接用 `curl` 重放任何一次呼叫；兩邊工程師/agent 上手快，因為框架都已經在這個 repo 出現過。
- **變困難的事**：「平台在處理主 bot 送來的事件時，途中回呼主 bot 查能力資料」這個巢狀呼叫模式，實作時必須小心避免死鎖——例如主 bot 處理能力查詢的 handler 不能持有任何平台端 dispatch handler 也需要的鎖／資源。這是實作階段的明確風險，下面列進 Action Items 的技術債務追蹤，不是這份 ADR 能一次解決的，需要在 L.5 step 3-4 實作時具體設計。
- **未來要重新評估的事**：目前假設同一台機器只跑一個 bot 實例，所以 port 用固定值＋環境變數覆蓋就夠；如果之後同一台主機要跑多個 bot 實例，固定 port 會撞號，需要動態配置——現在只需要確保「port 從一開始就是環境變數可覆蓋」，不用現在就解決動態配置問題。
- **新增的維運複雜度**：`rpc_protocol` 成為第三個「要保持同步」的東西（主 bot、平台、協定套件三方）。用 `PROTOCOL_VERSION` 常數＋每個請求都檢查版本、版本不符直接拒絕，防止兩邊悄悄長歪而不自知——這是分散式系統最常見的隱性 bug 來源，必須從第一天就有版本檢查，不是之後才補。

## Action Items

1. [ ] 建立獨立套件 `rpc_protocol/`（repo 根目錄，不 import 任一方 `core`）：定義每種訊息的 Pydantic schema（`DispatchEventRequest/Response`、`CapabilityLookupRequest/Response`、`ActionExecutionReport`、`HealthCheck`）、`PROTOCOL_VERSION` 常數、共用密鑰讀取工具、payload 大小上限常數。獨立測試（validate/reject 每種 schema 的合法與非法輸入），不依賴主 bot 或平台的任何其他程式碼。
2. [ ] 主 bot：新增 `aiohttp.web` 伺服器，只綁 `127.0.0.1`，暴露能力查詢端點；新增對平台事件分派端點的 `aiohttp.ClientSession` 呼叫，含 timeout／重試。
3. [ ] 平台：新增獨立 FastAPI app（跟 `web/admin` 分開，不同 port），暴露事件分派端點；新增 `httpx` 依賴，實作對主 bot 能力端點的呼叫。
4. [ ] 密鑰輪替機制：主 bot 每次啟動產生新密鑰，寫入權限受限的本機檔案；平台啟動時讀取，讀不到或不是最新版本就重試；雙方每個請求都驗證密鑰。
5. [ ] `correlation_id` 從 Discord 事件進入主 bot 的第一刻產生，貫穿之後每一次 RPC 呼叫，兩邊所有相關 log 行都要帶這個值。
6. [ ] `rpc_protocol` 自己的測試套件要能在完全不裝主 bot／平台任何其他依賴的情況下跑（驗證它真的是獨立套件，不是名義上獨立、實際上偷偷耦合）。
7. [ ] 巢狀回呼死鎖風險：實作 L.5 step 3-4（真的接上 capability request/action request）時，明確設計主 bot 能力查詢 handler 的並行模型（例如每個查詢在獨立 task 處理，不共用會被 dispatch handler 卡住的鎖／連線）。
8. [ ] 這份 ADR 完成即滿足 design.md L.5 step 1；下一步是 L.5 step 2（把 `rpc_protocol` 套件與雙端契約測試建好，不接真實 Discord）。
