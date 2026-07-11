"""FastAPI server: application factory, CORS, static file serving, startup/shutdown events."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.routes import _queue_manager, router

_logger = logging.getLogger(__name__)


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

    app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")

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
