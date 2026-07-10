"""
頁面路由，只負責回傳 Jinja2 樣板，不讀資料庫，見 design.md H.1：
「後端做成純 JSON API，前端是呼叫這個 API 的薄層，不是路由函式直接渲染 HTML」——
畫面上的資料一律由樣板內的 vanilla JS 呼叫 /api/... 取得。三個主要頁面（外掛/伺服器/狀態）
各自用左側清單／右側詳情的版面，選取項目透過 ?id= 查詢參數記錄，不用個別路徑。
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/")
async def index() -> RedirectResponse:
    """
    首頁導向狀態儀表板。
    """
    return RedirectResponse(url="/status")


@router.get("/plugins", response_class=HTMLResponse)
async def plugins_page(request: Request) -> HTMLResponse:
    """
    外掛頁：外掛列表＋詳情（審核、計價分類、各方案配額設定），內容全部由 JS 呼叫 /api/... 取得。
    """
    return templates.TemplateResponse(request, "plugins.html")


@router.get("/guilds", response_class=HTMLResponse)
async def guilds_page(request: Request) -> HTMLResponse:
    """
    伺服器頁：伺服器列表＋詳情（資源方案指定、封鎖名單、安裝管理），內容全部由 JS 呼叫 /api/... 取得。
    """
    return templates.TemplateResponse(request, "guilds.html")


@router.get("/status", response_class=HTMLResponse)
async def status_page(request: Request) -> HTMLResponse:
    """
    狀態頁：外掛狀態總覽與執行統計查詢儀表板，內容全部由 JS 呼叫 /api/... 取得。
    """
    return templates.TemplateResponse(request, "status.html")
