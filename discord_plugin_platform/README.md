# Discord Bot 外掛平台

Discord bot 的 Lua 外掛市集平台：外掛在資源受限的沙箱**子行程**執行，透過能力（capability）API 與 Discord 互動，平台操作者透過網頁後台／終端機審核與治理。

## 權威文件在哪裡

- `design.md`（**本機檔案，被 .gitignore 排除**，只存在主開發機的 main worktree）：完整架構、安全模型、能力 API 規格、Track 分工與驗收標準、歷次複查結論。**動手前必讀**；讀不到（其他機器／雲端環境）就向使用者索取，不得憑猜測動工。
- 專案根目錄 `CONVENTIONS.md`（同為本機檔案）：程式碼規範與多 agent 協作規則。
- 本 README 只是進版控的摘要，供拿不到上述文件的環境至少知道現況與硬約束。

## 目前狀態（2026-07，Track A-I 已完成合併）

- `core/`：資料層（repository／plugin_storage_repository／database）、manifest 驗證、配額、停權快取、訊息快取、dispatcher、能力 API、共用管理操作層（admin_operations）——完成
- `sandbox/`：Lua 5.4（lupa.lua54）沙箱，執行步數／記憶體上限，**每次執行 spawn 獨立子行程**，能力呼叫經 pipe RPC 回主行程——完成（經兩輪獨立安全複查，各抓到並修復真實逃逸漏洞）
- `bot_integration/`：事件監聽 Cog、排程消費、伺服器通知消費、終端機管理指令——完成，**但尚未接上任何真實 bot 行程**（見缺口 1）
- `web/admin/`：平台操作者後台（FastAPI + Jinja2，外掛／伺服器／狀態三工作區）——完成；綁 127.0.0.1、無登入（v1 刻意如此，`dependencies.py` 的 `get_current_operator()` 是未來加登入的唯一插入點）
- `web/public/`：公開市集——僅骨架（第四階段）
- `plugins_examples/`：temp_role_punishment 草稿——**從未實際執行過**（現有測試只做靜態檢查）
- 測試：270 個（含真子行程整合測試與故障注入），ruff 乾淨

## 執行方式

- 測試：`cd discord_plugin_platform && python -m pytest`（依賴：`pip install -e .[dev]`）
- 管理後台：`uvicorn web.admin.backend.main:app --host 127.0.0.1 --port 8010`（從本目錄啟動；注意下方資料庫路徑約束）
- bot 行程：**尚不存在**，完整規劃見 design.md Track J
- 合併回 main 前：專案根目錄 `python scripts/verify_before_merge.py`

## 硬性架構約束（違反會直接壞掉）

1. 本子專案與主專案都有頂層 `core/` 套件 → **絕不能與主 bot 同一個 Python 行程執行**，永遠是獨立行程（獨立 bot 身分、獨立 web 應用）。
2. SQLite 路徑 `data/plugin_platform.db` 是相對路徑：bot 行程與 web/admin 行程必須開到同一份檔案（Track J 規劃 `PLUGIN_PLATFORM_DB_PATH` 環境變數）。
3. 跨行程狀態同步一律「寫資料庫＋對方輪詢」（停權 10 秒、規劃中的指令同步 60 秒）；`core/admin_operations.py` 不得依賴 bot 行程記憶體。
4. 停權／封鎖採硬 cascade（直接刪安裝紀錄，不是停用旗標），下游一律全量重建狀態——新機制比照辦理。
5. 沙箱記憶體硬限制在 Windows 開發機無法完整驗證，正式（Linux）環境才能確認。
6. 沙箱固定用 `lupa.lua54`（lua55 的記憶體統計實測不可靠），不要改回預設。

## 已知的未來缺口（動工前先讀 design.md 對應章節）

1. **真實 bot 行程與 slash command 註冊**：design.md Track J 已完整規劃（含指令名稱全平台唯一、bulk upsert 全量覆蓋語意、target 路由、default_member_permissions 權限模型、defer/followup），未實作。
2. **範例外掛端到端實測**：併入 Track J.8。
3. **平台 i18n**：使用者可見文字（通知／互動回覆）目前寫死繁中，design.md Track K 已規劃。
4. **外掛版本升級流程**：新版本核准後既有安裝停留舊版，沒有升級與「新增能力重新同意」流程——市集開放前必須設計（安全關鍵）。
5. **作者自助提交／伺服器自助安裝**：第四階段 web/public；能力同意 UI 是安全關鍵。
6. 付款機制：明確排除在規劃外（pricing_tier 只是標籤）。
