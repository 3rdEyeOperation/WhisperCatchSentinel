"""FastAPI app that serves the local operator dashboard."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles


STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_BACKEND_BASE_URL = "http://127.0.0.1:8000"


def create_dashboard_app(*, backend_base_url: str | None = None) -> FastAPI:
    app = FastAPI(title="WhisperCatch Sentinel Dashboard", version="0.1.0")
    app.state.backend_base_url = (
        backend_base_url
        or os.getenv("WHISPERCATCH_BACKEND_URL", DEFAULT_BACKEND_BASE_URL)
    ).rstrip("/")
    app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        config = json.dumps({"backendBaseUrl": app.state.backend_base_url})
        return HTMLResponse(html.replace("__WCS_DASHBOARD_CONFIG__", config))

    return app


app = create_dashboard_app()
