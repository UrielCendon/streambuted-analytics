from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    analytics_port: int = Field(default=8085, alias="ANALYTICS_PORT")
    analytics_mongo_uri: str = Field(
        default="mongodb://analytics-mongo:27017",
        alias="ANALYTICS_MONGO_URI",
    )
    analytics_mongo_db: str = Field(
        default="streambuted_analytics",
        alias="ANALYTICS_MONGO_DB",
    )

    jwt_issuer: str = Field(
        default="http://identity-service:8081",
        alias="JWT_ISSUER",
    )
    jwt_jwks_url: str = Field(
        default="http://identity-service:8081/api/v1/auth/.well-known/jwks.json",
        alias="JWT_JWKS_URL",
    )
    jwt_audience: str | None = Field(default="streambuted-api", alias="JWT_AUDIENCE")

    rabbitmq_host: str = Field(default="rabbitmq", alias="RABBITMQ_HOST")
    rabbitmq_port: int = Field(default=5672, alias="RABBITMQ_PORT")
    rabbitmq_default_user: str = Field(
        default="streambuted",
        alias="RABBITMQ_DEFAULT_USER",
    )
    rabbitmq_default_pass: str = Field(default="", alias="RABBITMQ_DEFAULT_PASS")
    event_signing_secret: str = Field(default="", alias="EVENT_SIGNING_SECRET")
    analytics_playback_queue: str = Field(
        default="analytics.track-playback-counted",
        alias="ANALYTICS_PLAYBACK_QUEUE",
    )
    analytics_login_queue: str = Field(
        default="analytics.user-logged-in",
        alias="ANALYTICS_LOGIN_QUEUE",
    )
    analytics_catalog_queue: str = Field(
        default="analytics.catalog-events",
        alias="ANALYTICS_CATALOG_QUEUE",
    )

    cors_allowed_origins: str = Field(
        default="http://localhost:5173,http://localhost",
        alias="CORS_ALLOWED_ORIGINS",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("jwt_audience", mode="before")
    @classmethod
    def empty_string_to_none(cls, value: object) -> str | None:
        """Treat blank JWT_AUDIENCE as disabled audience validation."""
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return str(value)

    @property
    def allowed_cors_origins(self) -> list[str]:
        """Configured explicit browser origins allowed to call Analytics Service."""
        origins = [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]
        if not origins or "*" in origins:
            raise ValueError(
                "CORS_ALLOWED_ORIGINS must define explicit origins and cannot include '*'."
            )
        return origins


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""
    return Settings()
