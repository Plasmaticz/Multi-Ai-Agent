from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router as app_router
from app.config import Settings, get_settings
from app.local.store import LocalAppStore
from app.tools.storage import ProjectStore


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_settings.app_data_path.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title=resolved_settings.app_name, version="0.2.0")
    app.state.settings = resolved_settings
    app.state.project_store = ProjectStore()
    app.state.local_store = LocalAppStore(resolved_settings.sqlite_path)
    app.state.templates = Jinja2Templates(directory=str(Path("templates")))
    app.mount("/static", StaticFiles(directory="static"), name="static")
    app.include_router(app_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
