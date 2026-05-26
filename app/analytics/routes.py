from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request

from app.analytics.schemas import (
    AdminAnalyticsSummaryResponse,
    ArtistAnalyticsSummaryResponse,
    PublicDiscoverySummaryResponse,
)
from app.analytics.service import AnalyticsService
from app.auth.jwt_validator import JwtValidator
from app.auth.models import AuthenticatedUser, UserRole
from app.errors import AppError

router = APIRouter(prefix="/api/v1/analytics", tags=["Analytics"])


def get_analytics_service(request: Request) -> AnalyticsService:
    """Resolve the analytics service from application state."""
    return request.app.state.analytics_service


def get_jwt_validator(request: Request) -> JwtValidator:
    """Resolve the JWT validator from application state."""
    return request.app.state.jwt_validator


def get_current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    validator: JwtValidator = Depends(get_jwt_validator),
) -> AuthenticatedUser:
    """Resolve the authenticated user from the Authorization header."""
    return validator.validate_authorization_header(authorization)


def require_admin(user: AuthenticatedUser) -> None:
    """Ensure the user has administrator permissions."""
    if user.role != UserRole.ADMIN:
        raise AppError(403, "Forbidden", "Solo los administradores pueden acceder a este recurso.")


@router.get("/health")
async def public_health() -> dict[str, str]:
    """Return public Analytics Service health for gateway checks."""
    return {"status": "healthy", "service": "analytics-service"}


@router.get(
    "/discovery/summary",
    response_model=PublicDiscoverySummaryResponse,
)
async def get_discovery_summary(
    analytics_service: AnalyticsService = Depends(get_analytics_service),
) -> PublicDiscoverySummaryResponse:
    """Return public rankings for listener discovery."""
    return await analytics_service.get_public_discovery_summary()


@router.get(
    "/artists/{artist_id}/public-summary",
    response_model=ArtistAnalyticsSummaryResponse,
)
async def get_artist_public_summary(
    artist_id: str,
    analytics_service: AnalyticsService = Depends(get_analytics_service),
) -> ArtistAnalyticsSummaryResponse:
    """Return public track rankings for an artist profile."""
    return await analytics_service.get_artist_summary(artist_id)


@router.get(
    "/artists/{artist_id}/summary",
    response_model=ArtistAnalyticsSummaryResponse,
)
async def get_artist_summary(
    artist_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
) -> ArtistAnalyticsSummaryResponse:
    """Return analytics for an artist dashboard."""
    is_own_artist_dashboard = (
        current_user.role == UserRole.ARTIST
        and current_user.subject == artist_id
    )
    if current_user.role != UserRole.ADMIN and not is_own_artist_dashboard:
        raise AppError(403, "Forbidden", "Los artistas solo pueden consultar sus propias analiticas.")

    return await analytics_service.get_artist_summary(artist_id)


@router.get(
    "/admin/summary",
    response_model=AdminAnalyticsSummaryResponse,
)
async def get_admin_summary(
    current_user: AuthenticatedUser = Depends(get_current_user),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
) -> AdminAnalyticsSummaryResponse:
    """Return global analytics for administrators."""
    require_admin(current_user)
    return await analytics_service.get_admin_summary()
