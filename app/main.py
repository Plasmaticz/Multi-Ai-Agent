from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.routes import router as app_router
from app.config import Settings, get_settings
from app.local.store import LocalAppStore
from app.runtime import static_dir, templates_dir
from app.tools.storage import ProjectStore


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_settings.app_data_path.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title=resolved_settings.app_name, version="0.2.0")
    app.state.settings = resolved_settings
    app.state.project_store = ProjectStore()
    app.state.local_store = LocalAppStore(resolved_settings.sqlite_path)
    app.state.run_executor = ThreadPoolExecutor(max_workers=2)
    app.state.templates = Jinja2Templates(directory=str(templates_dir()))
    app.mount("/static", StaticFiles(directory=str(static_dir())), name="static")
    app.include_router(app_router)

    @app.api_route("/health", methods=["GET", "HEAD"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.on_event("shutdown")
    def shutdown_executor() -> None:
        app.state.run_executor.shutdown(wait=False, cancel_futures=True)

    return app


app = create_app()
