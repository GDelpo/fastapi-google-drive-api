"""Dashboard routes — serves the admin UI as HTML pages.

All pages require a valid token stored in localStorage.
The dashboard calls the existing JSON API under the hood.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.logger import get_logger

logger = get_logger(__name__)

# Cache for root folder name (resolved once, never changes)
_root_folder_name_cache: str | None = None

_DIR = Path(__file__).resolve().parent
_STATIC_DIR = _DIR / "static"
templates = Jinja2Templates(directory=str(_DIR / "templates"))

router = APIRouter(tags=["Dashboard"], include_in_schema=False)


# ============================================================================
# Static files (self-hosted Tailwind, Lucide, fonts)
# ============================================================================
_MIME = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
}


@router.get("/static/{path:path}")
async def dashboard_static(path: str):
    """Serve dashboard static assets (JS, CSS, fonts)."""
    file = _STATIC_DIR / path
    if not file.is_file() or not file.resolve().is_relative_to(_STATIC_DIR.resolve()):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Not found"}, status_code=404)
    media_type = _MIME.get(file.suffix, "application/octet-stream")
    return FileResponse(file, media_type=media_type)


def _base(request: Request) -> str:
    """Get the external base path (e.g., '/drive') from root_path."""
    return (request.scope.get("root_path") or "").rstrip("/")


# ============================================================================
# Shared template context
# ============================================================================

_NAV_ITEMS = [
    {"href_suffix": "/dashboard/", "icon": "bar-chart-3", "label": "Resumen", "key": "overview"},
    {"href_suffix": "/dashboard/files", "icon": "file", "label": "Archivos", "key": "files"},
    {"href_suffix": "/dashboard/folders", "icon": "folder", "label": "Carpetas", "key": "folders"},
]


def _ctx(request: Request, *, page_title: str, active_key: str, **extra) -> dict:
    """Build the common template context for authenticated pages."""
    base = _base(request)
    nav_items = [
        {**item, "href": base + item["href_suffix"], "active": item["key"] == active_key}
        for item in _NAV_ITEMS
    ]
    return {
        "request": request,
        "base": base,
        "service_name": "Drive",
        "service_icon": "hard-drive",
        "nav_items": nav_items,
        "page_title": page_title,
        **extra,
    }


# ============================================================================
# Pages
# ============================================================================


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    """Overview — stats + health + recent files."""
    global _root_folder_name_cache

    root_id = settings.google_root_folder_id or ""
    root_name = ""

    if root_id and _root_folder_name_cache is not None:
        root_name = _root_folder_name_cache
    elif root_id:
        # Resolve root folder name once (cached for process lifetime)
        try:
            from app.service import DriveService
            drive_svc: DriveService = getattr(request.app.state, "drive_service", None)
            if drive_svc:
                folder = await drive_svc.get_folder(root_id)
                root_name = folder.name
                _root_folder_name_cache = root_name
        except Exception as e:
            logger.warning("Could not resolve root folder name: %s", e)
            root_name = ""

    return templates.TemplateResponse(
        "overview.html",
        _ctx(
            request,
            page_title="Resumen",
            active_key="overview",
            root_folder_id=root_id,
            root_folder_name=root_name,
        ),
    )


@router.get("/files", response_class=HTMLResponse)
async def dashboard_files(request: Request):
    """Files list page."""
    return templates.TemplateResponse(
        "files.html",
        _ctx(request, page_title="Archivos", active_key="files"),
    )


@router.get("/files/{file_id}", response_class=HTMLResponse)
async def dashboard_file_detail(request: Request, file_id: str):
    """File detail page."""
    return templates.TemplateResponse(
        "file_detail.html",
        _ctx(request, page_title="Detalle de Archivo", active_key="files", file_id=file_id),
    )


@router.get("/folders", response_class=HTMLResponse)
async def dashboard_folders(request: Request):
    """Folders list page."""
    return templates.TemplateResponse(
        "folders.html",
        _ctx(request, page_title="Carpetas", active_key="folders"),
    )


@router.get("/folders/{folder_id}", response_class=HTMLResponse)
async def dashboard_folder_detail(request: Request, folder_id: str):
    """Folder detail page."""
    return templates.TemplateResponse(
        "folder_detail.html",
        _ctx(
            request,
            page_title="Detalle de Carpeta",
            active_key="folders",
            folder_id=folder_id,
            is_root_folder=(folder_id == (settings.google_root_folder_id or "")),
        ),
    )


@router.get("/login", response_class=HTMLResponse)
async def dashboard_login(request: Request):
    """Login page."""
    base = _base(request)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "base": base,
        "service_name": "Drive",
        "service_icon": "hard-drive",
    })
