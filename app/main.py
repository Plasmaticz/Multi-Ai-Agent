from fastapi import FastAPI

from app.api.routes import router as project_router
from app.config import get_settings

settings = get_settings()

app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(project_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
