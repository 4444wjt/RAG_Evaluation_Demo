"""Serve the built desktop and the existing lazy API on one local origin."""

from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def create_app() -> FastAPI:
    """Construct explicitly; importing this module never starts the API or build."""
    dist = PROJECT_ROOT / "frontend" / "dist"
    if not (dist / "index.html").is_file():
        raise RuntimeError(
            "Missing frontend/dist/index.html. Supply the built frontend/dist from "
            "the delivery package, or build it with the existing frontend dependencies."
        )

    from backend.api.app import create_app as create_api

    application = FastAPI(
        title="VeriScope Finance local desktop", docs_url=None,
        redoc_url=None, openapi_url=None,
    )
    application.mount("/api", create_api(), name="api")
    application.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return application
