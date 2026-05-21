from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from app.analytics.schemas import (
    AdminAnalyticsSummaryResponse,
    ArtistAnalyticsSummaryResponse,
    ContentType,
    CreateModerationReportRequest,
    ModerationReportResponse,
    PaginatedModerationReportsResponse,
    ReportStatus,
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
        raise AppError(403, "Forbidden", "Only administrators can access this endpoint.")


@router.get("/health")
async def public_health() -> dict[str, str]:
    """Return public Analytics Service health for gateway checks."""
    return {"status": "healthy", "service": "analytics-service"}


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
        raise AppError(403, "Forbidden", "Artists can only access their own analytics.")

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


@router.post(
    "/moderation/reports",
    response_model=ModerationReportResponse,
    status_code=201,
)
async def create_moderation_report(
    request: CreateModerationReportRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
) -> ModerationReportResponse:
    """Create a report that administrators can later review."""
    return await analytics_service.create_moderation_report(
        request=request,
        reporter_user_id=current_user.subject,
    )


@router.get(
    "/moderation/reports",
    response_model=PaginatedModerationReportsResponse,
)
async def list_moderation_reports(
    content_type: Annotated[ContentType | None, Query(alias="contentType")] = None,
    status: ReportStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    current_user: AuthenticatedUser = Depends(get_current_user),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
) -> PaginatedModerationReportsResponse:
    """Return moderation reports for administrators."""
    require_admin(current_user)
    return await analytics_service.list_moderation_reports(
        content_type=content_type,
        status=status,
        page=page,
        limit=limit,
    )


@router.get(
    "/moderation/tracks/reports",
    response_model=PaginatedModerationReportsResponse,
)
async def list_track_reports(
    status: ReportStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    current_user: AuthenticatedUser = Depends(get_current_user),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
) -> PaginatedModerationReportsResponse:
    """Return reported tracks for administrators."""
    require_admin(current_user)
    return await analytics_service.list_moderation_reports(
        content_type=ContentType.TRACK,
        status=status,
        page=page,
        limit=limit,
    )


@router.get(
    "/moderation/albums/reports",
    response_model=PaginatedModerationReportsResponse,
)
async def list_album_reports(
    status: ReportStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    current_user: AuthenticatedUser = Depends(get_current_user),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
) -> PaginatedModerationReportsResponse:
    """Return reported albums for administrators."""
    require_admin(current_user)
    return await analytics_service.list_moderation_reports(
        content_type=ContentType.ALBUM,
        status=status,
        page=page,
        limit=limit,
    )


@router.get(
    "/moderation/users/reports",
    response_model=PaginatedModerationReportsResponse,
)
async def list_user_reports(
    status: ReportStatus | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    current_user: AuthenticatedUser = Depends(get_current_user),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
) -> PaginatedModerationReportsResponse:
    """Return reported user accounts for administrators."""
    require_admin(current_user)
    return await analytics_service.list_moderation_reports(
        content_type=ContentType.USER,
        status=status,
        page=page,
        limit=limit,
    )
