"""Serve the finance-only build and a bounded subset of the existing lazy API.

The reverse proxy strips /finance/ before forwarding to this application.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PUBLIC_API = {
    ("GET", "/api/health"),
    ("POST", "/api/transaction-review/analyze"),
    ("POST", "/api/transaction-review/replay"),
}


class FinanceAPIScope:
    """Reject other API routes before dispatch or request-body consumption."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope["path"]
            if (path == "/api" or path.startswith("/api/")) and (scope["method"], path) not in _PUBLIC_API:
                await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app() -> FastAPI:
    """Construct without loading credentials, models or evaluation resources."""
    dist = Path(os.environ.get("VERISCOPE_FINANCE_DIST", PROJECT_ROOT / "frontend" / "dist-finance-public"))
    if not (dist / "index.html").is_file():
        raise RuntimeError("Missing finance public build index.html. Set VERISCOPE_FINANCE_DIST to the finance-only build directory.")

    from backend.api.app import create_app as create_api

    application = FastAPI(title="VeriScope Finance", docs_url=None, redoc_url=None, openapi_url=None)
    application.add_middleware(FinanceAPIScope)
    application.mount("/api", create_api(), name="api")
    application.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return application
