"""
頁面路由，只負責回傳 Jinja2 樣板，不讀資料庫，見 design.md H.1：
「後端做成純 JSON API，前端是呼叫這個 API 的薄層，不是路由函式直接渲染 HTML」——
畫面上的資料一律由樣板內的 vanilla JS 呼叫 /api/... 取得。
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """
    首頁，提供導覽連結與伺服器管理頁跳轉入口。
    """
    return templates.TemplateResponse(request, "index.html")


@router.get("/plugins", response_class=HTMLResponse)
async def plugins_page(request: Request) -> HTMLResponse:
    """
    外掛列表頁，內容由 JS 呼叫 GET /api/plugins 取得。
    """
    return templates.TemplateResponse(request, "plugins.html")


@router.get("/plugins/{plugin_id}", response_class=HTMLResponse)
async def plugin_detail_page(request: Request, plugin_id: str) -> HTMLResponse:
    """
    外掛詳情頁，只把 plugin_id 帶給樣板供 JS 組 API 路徑，不在這裡查資料庫。
    """
    return templates.TemplateResponse(request, "plugin_detail.html", {"plugin_id": plugin_id})


@router.get("/tiers", response_class=HTMLResponse)
async def tiers_page(request: Request) -> HTMLResponse:
    """
    資源方案管理頁。
    """
    return templates.TemplateResponse(request, "tiers.html")


@router.get("/guilds/{guild_id}", response_class=HTMLResponse)
async def guild_detail_page(request: Request, guild_id: int) -> HTMLResponse:
    """
    伺服器管理頁，只把 guild_id 帶給樣板供 JS 組 API 路徑，不在這裡查資料庫。
    """
    return templates.TemplateResponse(request, "guild_detail.html", {"guild_id": guild_id})
