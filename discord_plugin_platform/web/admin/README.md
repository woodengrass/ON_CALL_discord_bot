# 後台管理應用

平台操作者後台，`web/admin/backend/` 是一個**完全獨立的 FastAPI 專案**，不與 `web/public/`
共用任何程式碼或部署環境，見 design.md 第 3.5、H.1 節。

## 目前狀態

- 後端 JSON API 已完成（`/api/...`）：外掛審核（列表/詳情/核准/退回/封鎖/解封/計價分類）、
  資源方案 CRUD、外掛×方案設定、伺服器資源方案指定、伺服器封鎖名單、安裝管理（安裝/解除安裝/
  配額覆蓋/資源覆蓋）、全域停權/解除停權、稽核統計儀表板資料（`GET /api/plugins/{id}/stats`）。
- 每個路由都有對應的 `tests/web/admin/backend/` 整合測試（`TestClient`），只驗證路由正確呼叫
  `core/admin_operations.py`／`core/repository.py` 並回傳正確格式，不重複測一次業務邏輯本身。
- **前端尚未開始**：H.1 規劃的 Jinja2 + vanilla JS 薄層還沒做，目前只能透過 `/docs`
  （FastAPI 自動產生的 OpenAPI 介面）或直接呼叫 API 操作。

## 部署模型

- 開發環境啟動：`uvicorn web.admin.backend.main:app --host 127.0.0.1 --port 8001`
- **綁定 `127.0.0.1`，不對外網開放**，v1 不做真正登入驗證——`web/admin/backend/dependencies.py`
  的 `get_current_operator()` 是唯一的授權檢查入口，目前固定回傳 `"local-operator"`，之後要加
  操作者白名單登入時只需要改這個函式的實作。
- 跟 bot 主行程各自獨立啟動、獨立生命週期，見 design.md H.2：只有 `core/suspension.py` 的停權
  快取會有最多約 10 秒的生效延遲，配額/資源覆蓋、安裝/解除安裝都是即時生效。
- `bot_integration/admin_console.py` 終端機介面不因此退役，兩者並存、共用同一套
  `core/admin_operations.py`。

## 之後要做的

- 前端頁面（列表、詳情、表單）。
- 補上 I.10/I.11/I.12 提到的通知背景任務、資源額度耗盡通知等（若尚未在其他 Track 完成）。
