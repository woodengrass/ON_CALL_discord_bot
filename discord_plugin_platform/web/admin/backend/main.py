"""
獨立審核後台的 FastAPI 入口，見 design.md H.1：完全獨立的專案，不與 web/public/ 共用
任何程式碼或部署環境，v1 綁定 127.0.0.1、不做真正登入驗證。啟動方式（開發環境）：
uvicorn web.admin.backend.main:app --host 127.0.0.1 --port 8001
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.admin_operations import AdminOperationError
from core.manifest import ManifestValidationError
from web.admin.backend.routers import guilds, installations, plugins, stats, suspension, tiers

app = FastAPI(title="Discord Plugin Platform Admin")

app.include_router(plugins.router)
app.include_router(tiers.router)
app.include_router(guilds.router)
app.include_router(installations.router)
app.include_router(suspension.router)
app.include_router(stats.router)


@app.exception_handler(AdminOperationError)
async def handle_admin_operation_error(request: Request, error: AdminOperationError) -> JSONResponse:
    """
    將管理操作層的驗證失敗轉成 400 回應，不需要每個路由各自 try/except。
    """
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(ManifestValidationError)
async def handle_manifest_validation_error(request: Request, error: ManifestValidationError) -> JSONResponse:
    """
    將 manifest 驗證失敗轉成 400 回應。
    """
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(ValueError)
async def handle_value_error(request: Request, error: ValueError) -> JSONResponse:
    """
    將資料層的驗證失敗（未知欄位、型別錯誤等）轉成 400 回應。
    """
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.get("/health")
async def health_check() -> dict:
    """
    健康檢查端點，確認服務是否正常運作。

    Returns:
        dict，固定回傳 {"status": "ok"}
    """
    return {"status": "ok"}
