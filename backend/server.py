"""FastAPI server: application factory, CORS, static file serving, startup/shutdown events."""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.config import settings
from backend.routes import _queue_manager, router

_logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    """Static file server that lets the app-level 404 handler drive SPA fallback.

    Real files are served normally. Missing paths raise a 404 so the parent FastAPI
    app can decide whether to return JSON (for API/assets) or `index.html` (for SPA
    routes like `/jobs/<id>`).
    """

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                raise
            raise
        _apply_cache_control_headers(response, path)
        return response


def _apply_cache_control_headers(response: Response, path: str) -> None:
    """Apply safe cache policy for SPA HTML versus versioned static assets."""
    normalized = path.lstrip("/")
    if normalized in {"", "."} or normalized.endswith(".html") or normalized.endswith(".txt"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return
    if normalized.startswith("_next/static/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return
    response.headers["Cache-Control"] = "public, max-age=3600"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(title="RoboData", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api")

    @app.on_event("startup")
    async def _startup() -> None:
        """Ensure data dirs exist, recover prior crashes, and start the queue worker."""
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        settings.queue_dir.mkdir(parents=True, exist_ok=True)

        recovered = _queue_manager.recover_on_startup()
        if recovered:
            _logger.info("Startup recovery: marked %d RUNNING job(s) as FAILED", recovered)

        _queue_manager.start()

    frontend_out = Path("frontend/out")
    frontend_index = frontend_out / "index.html"

    @app.exception_handler(404)
    async def custom_404_handler(request, exc):
        path = request.url.path
        if path.startswith("/api") or "." in path.split("/")[-1]:
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        if frontend_index.exists():
            response = FileResponse(str(frontend_index))
            _apply_cache_control_headers(response, "index.html")
            return response
        return JSONResponse(
            status_code=404,
            content={"detail": "Frontend bundle not built yet"},
        )

    if frontend_out.exists():
        app.mount("/", SPAStaticFiles(directory=str(frontend_out), html=False), name="frontend")
    else:
        _logger.warning(
            "Frontend bundle directory %s is missing; skipping static mount",
            frontend_out,
        )

    return app


app: FastAPI = create_app()


def main() -> None:
    """Entry-point for `robodata` console script and `python -m backend.server`."""
    import uvicorn

    uvicorn.run(
        "backend.server:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )


if __name__ == "__main__":
    main()
