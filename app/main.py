import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.analytics.repository import MongoAnalyticsRepository
from app.analytics.routes import router as analytics_router
from app.analytics.service import AnalyticsService
from app.auth.jwt_validator import JwtValidator
from app.config import Settings, get_settings
from app.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.events.consumer import AnalyticsEventConsumer
from app.openapi import configure_openapi, register_swagger_docs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


def create_app(
    settings: Settings | None = None,
    repository: MongoAnalyticsRepository | None = None,
    jwt_validator: JwtValidator | None = None,
    start_consumer: bool = True,
) -> FastAPI:
    """Create and configure the FastAPI application."""
    app_settings = settings or get_settings()
    app_repository = repository or MongoAnalyticsRepository.from_settings(app_settings)
    app_jwt_validator = jwt_validator or JwtValidator(
        jwks_url=app_settings.jwt_jwks_url,
        issuer=app_settings.jwt_issuer,
        audience=app_settings.jwt_audience,
    )
    analytics_service = AnalyticsService(repository=app_repository)
    consumer: AnalyticsEventConsumer | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal consumer
        await maybe_await(app_repository.ensure_indexes())

        if start_consumer:
            loop = asyncio_get_running_loop()
            consumer = AnalyticsEventConsumer(
                host=app_settings.rabbitmq_host,
                port=app_settings.rabbitmq_port,
                username=app_settings.rabbitmq_default_user,
                password=app_settings.rabbitmq_default_pass,
                signing_secret=app_settings.event_signing_secret,
                playback_queue=app_settings.analytics_playback_queue,
                login_queue=app_settings.analytics_login_queue,
                catalog_queue=app_settings.analytics_catalog_queue,
                analytics_service=analytics_service,
                loop=loop,
            )
            consumer.start()

        try:
            yield
        finally:
            if consumer:
                consumer.stop()
            close_repository = getattr(app_repository, "close", None)
            if close_repository:
                close_repository()

    app = FastAPI(
        title="StreamButed Analytics Service",
        description=(
            "Proyecciones de analitica para descubrimiento publico, dashboards de artista "
            "y vistas administrativas."
        ),
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/v1/analytics/openapi.json",
        lifespan=lifespan,
    )
    register_swagger_docs(
        app,
        service_name="StreamButed Analytics Service",
        docs_url="/api/v1/analytics/docs",
        openapi_url="/api/v1/analytics/openapi.json",
    )
    configure_openapi(
        app,
        title="StreamButed Analytics Service",
        version="1.0.0",
        description=(
            "Proyecciones de analitica para descubrimiento publico, dashboards de artista "
            "y vistas administrativas alimentadas por eventos RabbitMQ."
        ),
        public_paths={
            "/api/v1/analytics/discovery/summary",
            "/api/v1/analytics/artists/{artist_id}/public-summary",
        },
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-Requested-With",
        ],
    )

    app.state.analytics_service = analytics_service
    app.state.jwt_validator = app_jwt_validator

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.get("/health")
    async def internal_health() -> dict[str, str]:
        """Return internal service health."""
        return {"status": "UP", "service": "analytics-service"}

    app.include_router(analytics_router)
    return app


async def maybe_await(value: Any) -> Any:
    """Await a value only when it is awaitable."""
    if inspect.isawaitable(value):
        return await value
    return value


def asyncio_get_running_loop() -> Any:
    """Return the current event loop without importing asyncio at module load time."""
    import asyncio

    return asyncio.get_running_loop()
