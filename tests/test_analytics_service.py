import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.analytics.service import AnalyticsService
from app.auth.models import AuthenticatedUser, UserRole
from app.main import create_app


class FakeRepository:
    def __init__(self) -> None:
        self.playbacks = []
        self.activities = []
        self.catalog_tracks = []
        self.catalog_albums = []
        self.catalog_artists = []

    async def ensure_indexes(self) -> None:
        return None

    async def record_playback(self, **kwargs):
        self.playbacks.append(kwargs)
        return True

    async def record_user_activity(self, **kwargs):
        self.activities.append(kwargs)
        return True

    async def upsert_artist_snapshot(self, **kwargs):
        self.catalog_artists.append(kwargs)
        return True

    async def upsert_album_snapshot(self, **kwargs):
        self.catalog_albums.append(kwargs)
        return True

    async def upsert_track_snapshot(self, **kwargs):
        self.catalog_tracks.append(kwargs)
        return True

    async def get_artist_track_metrics(self, artist_id):
        return [
            {
                "track_id": "track-1",
                "artist_id": artist_id,
                "track_title": "Luna",
                "artist_name": "Ada",
                "plays": 8,
            },
            {
                "track_id": "track-2",
                "artist_id": artist_id,
                "track_title": "Sol",
                "artist_name": "Ada",
                "plays": 3,
            },
        ]

    async def get_unique_listener_counts_by_track(self, _artist_id):
        return {"track-1": 5, "track-2": 2}

    async def get_artist_daily_averages(self, _artist_id):
        return (3.5, 5.5)

    async def get_total_play_count(self):
        return 11

    async def get_activity_user_count_since(self, _start_at):
        return 4

    async def get_top_tracks(self, _limit):
        return [
            {
                "track_id": "track-1",
                "artist_id": "artist-1",
                "track_title": "Luna",
                "artist_name": "Ada",
                "plays": 8,
            }
        ]

    async def get_top_artists(self, _limit):
        return [
            {
                "artist_id": "artist-1",
                "artist_name": "Ada",
                "plays": 11,
            }
        ]

    async def get_unique_listener_counts_by_artists(self, _artist_ids):
        return {"artist-1": 6}

    def close(self) -> None:
        return None


class FakeJwtValidator:
    def __init__(self, user):
        self.user = user

    def validate_authorization_header(self, _authorization_header):
        return self.user


def test_record_track_playback_persists_event_without_catalog_http() -> None:
    repository = FakeRepository()
    service = AnalyticsService(repository)

    was_recorded = asyncio.run(
        service.record_track_playback(
            {
                "eventId": "event-1",
                "eventType": "TrackPlaybackCounted",
                "userId": "listener-1",
                "trackId": "track-1",
                "countedAt": "2026-05-20T12:00:00Z",
                "positionSeconds": 31,
            }
        )
    )

    assert was_recorded is True
    assert repository.playbacks[0]["event_id"] == "event-1"
    assert repository.playbacks[0]["track_id"] == "track-1"
    assert "track" not in repository.playbacks[0]
    assert "artist" not in repository.playbacks[0]


def test_record_catalog_track_snapshot_updates_projection() -> None:
    repository = FakeRepository()
    service = AnalyticsService(repository)

    was_recorded = asyncio.run(
        service.record_catalog_snapshot(
            {
                "eventId": "event-2",
                "eventType": "CatalogTrackSnapshotUpdated",
                "trackId": "track-1",
                "artistId": "artist-1",
                "albumId": None,
                "title": "Luna",
                "genre": "Rock",
                "status": "PUBLICADO",
                "occurredAt": "2026-05-20T12:01:00Z",
            }
        )
    )

    assert was_recorded is True
    assert repository.catalog_tracks[0]["track_id"] == "track-1"
    assert repository.catalog_tracks[0]["title"] == "Luna"


def test_artist_summary_returns_totals_and_top_tracks() -> None:
    service = AnalyticsService(FakeRepository())

    summary = asyncio.run(service.get_artist_summary("artist-1"))

    assert summary.total_plays == 11
    assert summary.average_daily_unique_listeners == 3.5
    assert summary.average_daily_plays == 5.5
    assert [track.track_id for track in summary.top_tracks] == ["track-1", "track-2"]


def test_artist_endpoint_forbids_other_artist() -> None:
    app = create_app(
        repository=FakeRepository(),
        jwt_validator=FakeJwtValidator(
            AuthenticatedUser(subject="artist-2", role=UserRole.ARTIST)
        ),
        start_consumer=False,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/analytics/artists/artist-1/summary",
            headers={"Authorization": "Bearer test"},
        )

    assert response.status_code == 403


def test_admin_endpoint_returns_global_summary() -> None:
    app = create_app(
        repository=FakeRepository(),
        jwt_validator=FakeJwtValidator(
            AuthenticatedUser(subject="admin-1", role=UserRole.ADMIN)
        ),
        start_consumer=False,
    )

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/analytics/admin/summary",
            headers={"Authorization": "Bearer test"},
        )

    assert response.status_code == 200
    assert response.json()["totalPlays"] == 11
    assert response.json()["topArtists"][0]["artistName"] == "Ada"
