"""Application settings and configuration."""

from pathlib import Path

from pydantic import Field, SecretStr, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.
    
    This configuration is infrastructure-agnostic. The application runs
    the same way locally or behind any reverse proxy (Traefik, Nginx, etc.).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # =========================================================================
    # Environment
    # =========================================================================
    debug: bool = False
    environment: str | None = None

    # =========================================================================
    # Logging
    # =========================================================================
    log_level: str | None = None
    log_format: str = "json"  # json or text
    log_file: str | None = None

    # =========================================================================
    # Application Metadata
    # =========================================================================
    project_name: str = "Drive Service"
    project_version: str = "1.0.0"
    project_description: str = "Google Drive file management microservice"
    api_prefix: str = "/api/v1"

    # =========================================================================
    # CORS
    # =========================================================================
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    # =========================================================================
    # Google Drive
    # =========================================================================
    google_credentials_path: str = "/app/credentials/drive_credentials.json"
    google_root_folder_id: str = ""
    google_scopes: list[str] = Field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
    )

    # OAuth2 (personal account) — takes precedence over service account
    # when all three are set.  Obtain refresh_token once via refresh_token.py.
    google_oauth_client_id: str = ""
    google_oauth_client_secret: SecretStr = SecretStr("")
    google_oauth_refresh_token: str = ""

    @property
    def use_oauth(self) -> bool:
        """True when OAuth2 credentials are fully configured."""
        return bool(
            self.google_oauth_client_id
            and self.google_oauth_client_secret.get_secret_value()
            and self.google_oauth_refresh_token
        )

    # =========================================================================
    # Identity Service (for token validation)
    # =========================================================================
    identity_service_url: str = "http://localhost:8080/api/v1"

    # External URL for Swagger UI tokenUrl (browser needs to reach this)
    # Production: http://192.0.2.10/identity/api/v1
    # If not set, falls back to identity_service_url
    identity_external_url: str = ""
    
    # Skip authentication (for dev when identity is not accessible)
    skip_auth: bool = False

    @computed_field
    @property
    def identity_login_url(self) -> str:
        """URL for Swagger UI login (must be browser-accessible)."""
        base = self.identity_external_url or self.identity_service_url
        return f"{base}/login"

    @computed_field
    @property
    def identity_me_url(self) -> str:
        """URL to validate tokens and get user info."""
        return f"{self.identity_service_url}/me"

    # =========================================================================
    # Rate Limiting
    # =========================================================================
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # =========================================================================
    # Upload Settings
    # =========================================================================
    max_upload_size_mb: int = 100
    allowed_mime_types: list[str] = Field(
        default_factory=lambda: [
            "application/pdf",
            "image/jpeg",
            "image/png",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "text/plain",
            "text/csv",
        ]
    )

    # =========================================================================
    # Validation
    # =========================================================================
    @model_validator(mode="after")
    def validate_settings(self):
        """Derive values and validate configuration."""
        # Derive environment from debug if not set
        if self.environment is None:
            self.environment = "development" if self.debug else "production"

        # Derive log_level from debug if not set
        if self.log_level is None:
            self.log_level = "DEBUG" if self.debug else "INFO"

        return self


settings = Settings()
