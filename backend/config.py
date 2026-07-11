"""Application configuration loaded from environment variables via Pydantic Settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application-wide configuration sourced from environment variables.

    Auth passwords follow a backward-compatible pattern:
        - *judge_access_password* is the primary env var for judge logins.
          If unset, falls back to the legacy *access_password*.
        - *admin_access_password* is the primary env var for admin logins.
          No fallback — admins must set it explicitly.
        - *access_password* is a legacy field retained for transition only.
    """

    access_password: str | None = None
    judge_access_password: str | None = None
    admin_access_password: str | None = None

    jwt_secret_key: str
    jwt_expiry_hours: int = 24

    max_video_duration_seconds: int = 30
    max_video_size_mb: int = 100

    data_dir: Path = Path("./data")

    output_dir: Path = Path("./outputs")
    upload_dir: Path = Path("./uploads")

    target_robot: str = "franka_panda"

    host: str = "0.0.0.0"
    port: int = 8000

    log_level: str = "INFO"

    featherless_api_key: str | None = None
    featherless_base_url: str = "https://api.featherless.ai"
    featherless_model_name: str = "kimi-k2"
    daytona_api_key: str | None = None
    daytona_base_url: str = "https://app.daytona.io"
    daytona_proxy_base_url: str = "https://proxy.app.daytona.io"
    daytona_project_id: str | None = None
    review_timeout_seconds: int = 60
    review_max_context_chars: int = 24000
    review_stream_chunk_chars: int = 240

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    @property
    def jobs_dir(self) -> Path:
        """Root directory for per-job data under data/jobs/."""
        return self.data_dir / "jobs"

    @property
    def queue_dir(self) -> Path:
        """Persisted FIFO queue directory under data/queue/."""
        return self.data_dir / "queue"

    @property
    def effective_judge_password(self) -> str:
        """Return the judge password, falling back to legacy access_password.

        Raises:
            ValueError: If neither judge_access_password nor access_password is configured.
        """
        password = self.judge_access_password or self.access_password
        if password is None:
            raise ValueError(
                "No judge password configured. "
                "Set JUDGE_ACCESS_PASSWORD (or ACCESS_PASSWORD as legacy fallback)."
            )
        return password

    @property
    def has_admin_password(self) -> bool:
        """Return True if an admin password is explicitly configured."""
        return self.admin_access_password is not None

    @property
    def review_execution_mode(self) -> str:
        """Return provider execution mode for reviews.

        Returns ``featherless_daytona`` only when both provider and sandbox
        credentials are configured; otherwise returns ``local_fallback`` so the
        hackathon app still produces deterministic review artifacts.
        """
        if self.featherless_api_key and self.daytona_api_key:
            return "featherless_daytona"
        return "local_fallback"

    @property
    def review_provider_name(self) -> str:
        """Return the configured review provider label."""
        return "featherless"

    @property
    def review_sandbox_name(self) -> str:
        """Return the configured sandbox label."""
        return "daytona"

    @property
    def review_model_name(self) -> str:
        """Return the configured model name used for external reviews."""
        return self.featherless_model_name


settings: Settings = Settings()
