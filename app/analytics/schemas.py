from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContentType(str, Enum):
    """Moderation content types known by Analytics Service."""

    TRACK = "TRACK"
    ALBUM = "ALBUM"
    USER = "USER"


class ReportStatus(str, Enum):
    """Moderation report lifecycle states."""

    REPORTED = "REPORTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class TrackPlaybackCountedEvent(BaseModel):
    """Domain event emitted when a playback should count in analytics."""

    event_id: str = Field(default_factory=lambda: str(uuid4()), alias="eventId")
    event_type: str = Field(default="TrackPlaybackCounted", alias="eventType")
    user_id: str = Field(..., alias="userId")
    track_id: str = Field(..., alias="trackId")
    counted_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="countedAt")
    position_seconds: float = Field(default=0, ge=0, alias="positionSeconds")

    model_config = ConfigDict(populate_by_name=True)


class UserLoggedInEvent(BaseModel):
    """Domain event emitted when a user starts a session."""

    event_id: str = Field(default_factory=lambda: str(uuid4()), alias="eventId")
    event_type: str = Field(default="UserLoggedInEvent", alias="eventType")
    user_id: str = Field(..., alias="userId")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="occurredAt")

    model_config = ConfigDict(populate_by_name=True)


class CatalogArtistSnapshotEvent(BaseModel):
    """Catalog event carrying the current artist snapshot for projections."""

    event_id: str = Field(default_factory=lambda: str(uuid4()), alias="eventId")
    event_type: str = Field(default="CatalogArtistSnapshotUpdated", alias="eventType")
    artist_id: str = Field(..., alias="artistId")
    display_name: str = Field(..., alias="displayName")
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="occurredAt")

    model_config = ConfigDict(populate_by_name=True)


class CatalogAlbumSnapshotEvent(BaseModel):
    """Catalog event carrying the current album snapshot for projections."""

    event_id: str = Field(default_factory=lambda: str(uuid4()), alias="eventId")
    event_type: str = Field(default="CatalogAlbumSnapshotUpdated", alias="eventType")
    album_id: str = Field(..., alias="albumId")
    artist_id: str = Field(..., alias="artistId")
    title: str
    status: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="occurredAt")

    model_config = ConfigDict(populate_by_name=True)


class CatalogTrackSnapshotEvent(BaseModel):
    """Catalog event carrying the current track snapshot for projections."""

    event_id: str = Field(default_factory=lambda: str(uuid4()), alias="eventId")
    event_type: str = Field(default="CatalogTrackSnapshotUpdated", alias="eventType")
    track_id: str = Field(..., alias="trackId")
    artist_id: str = Field(..., alias="artistId")
    album_id: str | None = Field(default=None, alias="albumId")
    title: str
    genre: str | None = None
    status: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="occurredAt")

    model_config = ConfigDict(populate_by_name=True)


class TrackMetricResponse(BaseModel):
    """Track metric row for dashboards."""

    track_id: str = Field(..., alias="trackId")
    title: str
    artist_id: str | None = Field(default=None, alias="artistId")
    artist_name: str | None = Field(default=None, alias="artistName")
    plays: int
    unique_listeners: int = Field(default=0, alias="uniqueListeners")

    model_config = ConfigDict(populate_by_name=True)


class ArtistMetricResponse(BaseModel):
    """Artist metric row for admin dashboards."""

    artist_id: str = Field(..., alias="artistId")
    artist_name: str = Field(..., alias="artistName")
    plays: int
    unique_listeners: int = Field(default=0, alias="uniqueListeners")

    model_config = ConfigDict(populate_by_name=True)


class ArtistAnalyticsSummaryResponse(BaseModel):
    """Aggregated metrics for an artist dashboard."""

    artist_id: str = Field(..., alias="artistId")
    total_plays: int = Field(..., alias="totalPlays")
    tracks: list[TrackMetricResponse]
    top_tracks: list[TrackMetricResponse] = Field(..., alias="topTracks")
    average_daily_unique_listeners: float = Field(..., alias="averageDailyUniqueListeners")
    average_daily_plays: float = Field(..., alias="averageDailyPlays")

    model_config = ConfigDict(populate_by_name=True)


class AdminAnalyticsSummaryResponse(BaseModel):
    """Aggregated metrics for the admin analytics dashboard."""

    daily_active_users: int = Field(..., alias="dailyActiveUsers")
    monthly_active_users: int = Field(..., alias="monthlyActiveUsers")
    total_plays: int = Field(..., alias="totalPlays")
    top_tracks: list[TrackMetricResponse] = Field(..., alias="topTracks")
    top_artists: list[ArtistMetricResponse] = Field(..., alias="topArtists")

    model_config = ConfigDict(populate_by_name=True)


class CreateModerationReportRequest(BaseModel):
    """Request body used to create a moderation report."""

    content_type: ContentType = Field(..., alias="contentType")
    content_id: str = Field(..., min_length=1, max_length=120, alias="contentId")
    content_title: str = Field(default="Contenido reportado", max_length=240, alias="contentTitle")
    reason: str = Field(..., min_length=3, max_length=500)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("content_title", mode="before")
    @classmethod
    def default_blank_title(cls, value: object) -> str:
        """Use a stable fallback when clients omit a title."""
        if value is None:
            return "Contenido reportado"
        text = str(value).strip()
        return text or "Contenido reportado"


class ModerationReportResponse(BaseModel):
    """Moderation report returned to administrators."""

    report_id: str = Field(..., alias="reportId")
    content_type: ContentType = Field(..., alias="contentType")
    content_id: str = Field(..., alias="contentId")
    content_title: str = Field(..., alias="contentTitle")
    reporter_user_id: str = Field(..., alias="reporterUserId")
    reason: str
    status: ReportStatus
    created_at: datetime = Field(..., alias="createdAt")
    updated_at: datetime = Field(..., alias="updatedAt")

    model_config = ConfigDict(populate_by_name=True)


class PaginationResponse(BaseModel):
    """Pagination metadata for list responses."""

    page: int
    limit: int
    total: int
    total_pages: int = Field(..., alias="totalPages")

    model_config = ConfigDict(populate_by_name=True)


class PaginatedModerationReportsResponse(BaseModel):
    """Paginated moderation report response."""

    data: list[ModerationReportResponse]
    pagination: PaginationResponse

    model_config = ConfigDict(populate_by_name=True)


def unwrap_event_payload(payload: dict[str, object]) -> dict[str, object]:
    """Normalize wrapped and flat RabbitMQ event payloads."""
    nested_payload = payload.get("payload")
    if not isinstance(nested_payload, dict):
        return payload

    merged_payload: dict[str, object] = dict(nested_payload)
    if "eventId" in payload and "eventId" not in merged_payload:
        merged_payload["eventId"] = payload["eventId"]
    if "eventType" in payload and "eventType" not in merged_payload:
        merged_payload["eventType"] = payload["eventType"]
    if "publishedAt" in payload:
        merged_payload.setdefault("countedAt", payload["publishedAt"])
        merged_payload.setdefault("occurredAt", payload["publishedAt"])
    return merged_payload
