from datetime import UTC, datetime
from typing import Any, Protocol

from app.analytics.repository import MongoAnalyticsRepository
from app.analytics.schemas import (
    AdminAnalyticsSummaryResponse,
    AlbumMetricResponse,
    ArtistAnalyticsSummaryResponse,
    ArtistMetricResponse,
    CatalogAlbumSnapshotEvent,
    CatalogArtistSnapshotEvent,
    CatalogTrackSnapshotEvent,
    TrackMetricResponse,
    TrackPlaybackCountedEvent,
    PublicDiscoverySummaryResponse,
    UserLoggedInEvent,
    unwrap_event_payload,
)


class AnalyticsRepository(Protocol):
    """Repository contract required by AnalyticsService."""

    async def record_playback(
        self,
        event_id: str,
        user_id: str,
        track_id: str,
        counted_at: datetime,
        position_seconds: float,
    ) -> bool: ...

    async def upsert_artist_snapshot(
        self,
        event_id: str,
        artist_id: str,
        display_name: str,
        occurred_at: datetime,
    ) -> bool: ...

    async def upsert_album_snapshot(
        self,
        event_id: str,
        album_id: str,
        artist_id: str,
        title: str,
        cover_asset_id: str | None,
        status: str,
        occurred_at: datetime,
    ) -> bool: ...

    async def upsert_track_snapshot(
        self,
        event_id: str,
        track_id: str,
        artist_id: str,
        album_id: str | None,
        title: str,
        genre: str | None,
        status: str,
        occurred_at: datetime,
    ) -> bool: ...

    async def record_user_activity(
        self,
        event_id: str,
        user_id: str,
        occurred_at: datetime,
        activity_type: str,
    ) -> bool: ...

    async def get_artist_track_metrics(self, artist_id: str) -> list[dict[str, Any]]: ...

    async def get_unique_listener_counts_by_track(self, artist_id: str) -> dict[str, int]: ...

    async def get_artist_daily_averages(self, artist_id: str) -> tuple[float, float]: ...

    async def get_total_play_count(self) -> int: ...

    async def get_activity_user_count_since(self, start_at: datetime) -> int: ...

    async def get_top_tracks(self, limit: int) -> list[dict[str, Any]]: ...

    async def get_top_artists(self, limit: int) -> list[dict[str, Any]]: ...

    async def get_top_albums(self, limit: int) -> list[dict[str, Any]]: ...

    async def get_unique_listener_counts_by_artists(self, artist_ids: list[str]) -> dict[str, int]: ...


class AnalyticsService:
    """Application service for analytics projections and dashboard queries."""

    def __init__(
        self,
        repository: AnalyticsRepository | MongoAnalyticsRepository,
    ) -> None:
        """Create an analytics service."""
        self._repository = repository

    async def record_track_playback(self, payload: dict[str, object]) -> bool:
        """Process one TrackPlaybackCounted event."""
        event = TrackPlaybackCountedEvent.model_validate(unwrap_event_payload(payload))
        return await self._repository.record_playback(
            event_id=event.event_id,
            user_id=event.user_id,
            track_id=event.track_id,
            counted_at=event.counted_at,
            position_seconds=event.position_seconds,
        )

    async def record_catalog_snapshot(self, payload: dict[str, object]) -> bool:
        """Process one Catalog snapshot event for local analytics projections."""
        unwrapped = unwrap_event_payload(payload)
        event_type = str(unwrapped.get("eventType") or "")
        if event_type == "CatalogArtistSnapshotUpdated":
            event = CatalogArtistSnapshotEvent.model_validate(unwrapped)
            return await self._repository.upsert_artist_snapshot(
                event_id=event.event_id,
                artist_id=event.artist_id,
                display_name=event.display_name,
                occurred_at=event.occurred_at,
            )
        if event_type == "CatalogAlbumSnapshotUpdated":
            event = CatalogAlbumSnapshotEvent.model_validate(unwrapped)
            return await self._repository.upsert_album_snapshot(
                event_id=event.event_id,
                album_id=event.album_id,
                artist_id=event.artist_id,
                title=event.title,
                cover_asset_id=event.cover_asset_id,
                status=event.status,
                occurred_at=event.occurred_at,
            )
        if event_type == "CatalogTrackSnapshotUpdated":
            event = CatalogTrackSnapshotEvent.model_validate(unwrapped)
            return await self._repository.upsert_track_snapshot(
                event_id=event.event_id,
                track_id=event.track_id,
                artist_id=event.artist_id,
                album_id=event.album_id,
                title=event.title,
                genre=event.genre,
                status=event.status,
                occurred_at=event.occurred_at,
            )

        raise ValueError(f"Unsupported Catalog event type: {event_type}")

    async def record_user_login(self, payload: dict[str, object]) -> bool:
        """Process one UserLoggedInEvent."""
        event = UserLoggedInEvent.model_validate(unwrap_event_payload(payload))
        return await self._repository.record_user_activity(
            event_id=f"login:{event.event_id}",
            user_id=event.user_id,
            occurred_at=event.occurred_at,
            activity_type="LOGIN",
        )

    async def get_artist_summary(self, artist_id: str) -> ArtistAnalyticsSummaryResponse:
        """Return metrics for one artist dashboard."""
        track_rows = await self._repository.get_artist_track_metrics(artist_id)
        unique_listeners_by_track = await self._repository.get_unique_listener_counts_by_track(
            artist_id
        )
        average_unique, average_plays = await self._repository.get_artist_daily_averages(
            artist_id
        )

        tracks = [
            map_track_metric(row, unique_listeners_by_track.get(str(row.get("track_id")), 0))
            for row in track_rows
        ]
        total_plays = sum(track.plays for track in tracks)

        return ArtistAnalyticsSummaryResponse(
            artistId=artist_id,
            totalPlays=total_plays,
            tracks=tracks,
            topTracks=tracks[:10],
            averageDailyUniqueListeners=average_unique,
            averageDailyPlays=average_plays,
        )

    async def get_admin_summary(self) -> AdminAnalyticsSummaryResponse:
        """Return global metrics for the admin dashboard."""
        now = datetime.now(UTC)
        start_of_day = datetime(now.year, now.month, now.day, tzinfo=UTC)
        start_of_month = datetime(now.year, now.month, 1, tzinfo=UTC)

        top_track_rows = await self._repository.get_top_tracks(10)
        top_artist_rows = await self._repository.get_top_artists(10)
        artist_ids = [
            str(row.get("artist_id"))
            for row in top_artist_rows
            if row.get("artist_id")
        ]
        unique_listeners_by_artist = await self._repository.get_unique_listener_counts_by_artists(
            artist_ids
        )

        return AdminAnalyticsSummaryResponse(
            dailyActiveUsers=await self._repository.get_activity_user_count_since(start_of_day),
            monthlyActiveUsers=await self._repository.get_activity_user_count_since(start_of_month),
            totalPlays=await self._repository.get_total_play_count(),
            topTracks=[map_track_metric(row, 0) for row in top_track_rows],
            topArtists=[
                map_artist_metric(
                    row,
                    unique_listeners_by_artist.get(str(row.get("artist_id")), 0),
                )
                for row in top_artist_rows
            ],
        )

    async def get_public_discovery_summary(self) -> PublicDiscoverySummaryResponse:
        """Return public rankings for listener discovery surfaces."""
        top_album_rows = await self._repository.get_top_albums(10)
        top_artist_rows = await self._repository.get_top_artists(10)
        artist_ids = [
            str(row.get("artist_id"))
            for row in top_artist_rows
            if row.get("artist_id")
        ]
        unique_listeners_by_artist = await self._repository.get_unique_listener_counts_by_artists(
            artist_ids
        )

        return PublicDiscoverySummaryResponse(
            topAlbums=[map_album_metric(row) for row in top_album_rows],
            topArtists=[
                map_artist_metric(
                    row,
                    unique_listeners_by_artist.get(str(row.get("artist_id")), 0),
                )
                for row in top_artist_rows
            ],
        )

def map_track_metric(row: dict[str, Any], unique_listeners: int) -> TrackMetricResponse:
    """Map a Mongo track projection into an API response."""
    return TrackMetricResponse(
        trackId=str(row.get("track_id") or ""),
        title=str(row.get("track_title") or "Unknown track"),
        artistId=optional_string(row.get("artist_id")),
        artistName=optional_string(row.get("artist_name")),
        plays=int(row.get("plays") or 0),
        uniqueListeners=unique_listeners,
    )


def map_artist_metric(row: dict[str, Any], unique_listeners: int) -> ArtistMetricResponse:
    """Map a Mongo artist projection into an API response."""
    return ArtistMetricResponse(
        artistId=str(row.get("artist_id") or ""),
        artistName=str(row.get("artist_name") or "Unknown artist"),
        plays=int(row.get("plays") or 0),
        uniqueListeners=unique_listeners,
    )


def map_album_metric(row: dict[str, Any]) -> AlbumMetricResponse:
    """Map a Mongo album aggregate into an API response."""
    return AlbumMetricResponse(
        albumId=str(row.get("album_id") or ""),
        artistId=str(row.get("artist_id") or ""),
        title=str(row.get("title") or "Unknown album"),
        artistName=optional_string(row.get("artist_name")),
        coverAssetId=optional_string(row.get("cover_asset_id")),
        plays=int(row.get("plays") or 0),
    )


def optional_string(value: object) -> str | None:
    """Convert a possible value into a non-empty string."""
    if value is None:
        return None
    converted = str(value).strip()
    return converted or None
