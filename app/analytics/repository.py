from datetime import UTC, datetime
from typing import Any

from app.config import Settings

PLAYBACK_EVENTS_COLLECTION = "playback_events"
ACTIVITY_EVENTS_COLLECTION = "activity_events"
TRACK_METRICS_COLLECTION = "track_metrics"
ARTIST_METRICS_COLLECTION = "artist_metrics"
CATALOG_ARTISTS_COLLECTION = "catalog_artists"
CATALOG_ALBUMS_COLLECTION = "catalog_albums"
CATALOG_TRACKS_COLLECTION = "catalog_tracks"


class MongoAnalyticsRepository:
    """MongoDB repository for analytics projections."""

    def __init__(self, client: Any, database_name: str) -> None:
        """Create the repository."""
        self._client = client
        self._database = client[database_name]
        self._playback_events = self._database[PLAYBACK_EVENTS_COLLECTION]
        self._activity_events = self._database[ACTIVITY_EVENTS_COLLECTION]
        self._track_metrics = self._database[TRACK_METRICS_COLLECTION]
        self._artist_metrics = self._database[ARTIST_METRICS_COLLECTION]
        self._catalog_artists = self._database[CATALOG_ARTISTS_COLLECTION]
        self._catalog_albums = self._database[CATALOG_ALBUMS_COLLECTION]
        self._catalog_tracks = self._database[CATALOG_TRACKS_COLLECTION]

    @classmethod
    def from_settings(cls, settings: Settings) -> "MongoAnalyticsRepository":
        """Create the repository from runtime settings."""
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(
            settings.analytics_mongo_uri,
            uuidRepresentation="standard",
        )
        return cls(client=client, database_name=settings.analytics_mongo_db)

    async def ensure_indexes(self) -> None:
        """Create MongoDB indexes required by analytics queries."""
        await self._playback_events.create_index("event_id", unique=True)
        await self._playback_events.create_index([("artist_id", 1), ("counted_at", -1)])
        await self._playback_events.create_index([("track_id", 1), ("counted_at", -1)])
        await self._playback_events.create_index([("user_id", 1), ("counted_at", -1)])
        await self._activity_events.create_index("event_id", unique=True)
        await self._activity_events.create_index([("user_id", 1), ("occurred_at", -1)])
        await self._track_metrics.create_index("track_id", unique=True)
        await self._track_metrics.create_index([("artist_id", 1), ("plays", -1)])
        await self._artist_metrics.create_index("artist_id", unique=True)
        await self._artist_metrics.create_index("plays")
        await self._catalog_artists.create_index("artist_id", unique=True)
        await self._catalog_albums.create_index("album_id", unique=True)
        await self._catalog_tracks.create_index("track_id", unique=True)
        await self._catalog_tracks.create_index([("artist_id", 1), ("status", 1)])

    async def record_playback(
        self,
        event_id: str,
        user_id: str,
        track_id: str,
        counted_at: datetime,
        position_seconds: float,
    ) -> bool:
        """Persist one playback event and update aggregate projections once."""
        normalized_counted_at = ensure_utc(counted_at)
        track = await self._catalog_tracks.find_one({"track_id": track_id})
        artist_id = optional_string(track.get("artist_id")) if track else None
        album_id = optional_string(track.get("album_id")) if track else None
        track_status = str(track.get("status") or "PUBLICADO") if track else "PUBLICADO"
        track_title = str(track.get("title") or "Unknown track") if track else "Unknown track"
        artist_name = await self._resolve_artist_name(artist_id)

        playback_document = {
            "event_id": event_id,
            "user_id": user_id,
            "track_id": track_id,
            "artist_id": artist_id,
            "album_id": album_id,
            "track_title": track_title,
            "artist_name": artist_name,
            "track_status": track_status,
            "position_seconds": position_seconds,
            "counted_at": normalized_counted_at,
            "created_at": datetime.now(UTC),
        }
        insert_result = await self._playback_events.update_one(
            {"event_id": event_id},
            {"$setOnInsert": playback_document},
            upsert=True,
        )
        if insert_result.upserted_id is None:
            return False

        await self._activity_events.update_one(
            {"event_id": f"playback:{event_id}"},
            {
                "$setOnInsert": {
                    "event_id": f"playback:{event_id}",
                    "user_id": user_id,
                    "activity_type": "PLAYBACK",
                    "occurred_at": normalized_counted_at,
                    "created_at": datetime.now(UTC),
                }
            },
            upsert=True,
        )
        await self._track_metrics.update_one(
            {"track_id": track_id},
            {
                "$inc": {"plays": 1},
                "$set": {
                    "artist_id": artist_id,
                    "album_id": album_id,
                    "track_title": track_title,
                    "artist_name": artist_name,
                    "status": track_status,
                    "updated_at": datetime.now(UTC),
                },
                "$setOnInsert": {"created_at": datetime.now(UTC)},
            },
            upsert=True,
        )

        if artist_id and track_status == "PUBLICADO":
            await self._artist_metrics.update_one(
                {"artist_id": artist_id},
                {
                    "$inc": {"plays": 1},
                    "$set": {
                        "artist_name": artist_name,
                        "updated_at": datetime.now(UTC),
                    },
                    "$setOnInsert": {"created_at": datetime.now(UTC)},
                },
                upsert=True,
            )
        return True

    async def upsert_artist_snapshot(
        self,
        event_id: str,
        artist_id: str,
        display_name: str,
        occurred_at: datetime,
    ) -> bool:
        """Store the latest artist projection from Catalog events."""
        result = await self._catalog_artists.update_one(
            {"artist_id": artist_id},
            {
                "$set": {
                    "artist_id": artist_id,
                    "display_name": display_name,
                    "last_event_id": event_id,
                    "updated_at": ensure_utc(occurred_at),
                },
                "$setOnInsert": {"created_at": datetime.now(UTC)},
            },
            upsert=True,
        )
        await self._track_metrics.update_many(
            {"artist_id": artist_id},
            {"$set": {"artist_name": display_name, "updated_at": datetime.now(UTC)}},
        )
        await self._playback_events.update_many(
            {"artist_id": artist_id},
            {"$set": {"artist_name": display_name}},
        )
        await self._rebuild_artist_metric(artist_id)
        return result.upserted_id is not None or result.modified_count > 0

    async def upsert_album_snapshot(
        self,
        event_id: str,
        album_id: str,
        artist_id: str,
        title: str,
        cover_asset_id: str | None,
        status: str,
        occurred_at: datetime,
    ) -> bool:
        """Store the latest album projection from Catalog events."""
        result = await self._catalog_albums.update_one(
            {"album_id": album_id},
            {
                "$set": {
                    "album_id": album_id,
                    "artist_id": artist_id,
                    "title": title,
                    "cover_asset_id": cover_asset_id,
                    "status": status,
                    "last_event_id": event_id,
                    "updated_at": ensure_utc(occurred_at),
                },
                "$setOnInsert": {"created_at": datetime.now(UTC)},
            },
            upsert=True,
        )
        return result.upserted_id is not None or result.modified_count > 0

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
    ) -> bool:
        """Store the latest track projection and repair aggregate labels."""
        previous = await self._catalog_tracks.find_one({"track_id": track_id})
        previous_artist_id = optional_string(previous.get("artist_id")) if previous else None
        artist_name = await self._resolve_artist_name(artist_id)

        result = await self._catalog_tracks.update_one(
            {"track_id": track_id},
            {
                "$set": {
                    "track_id": track_id,
                    "artist_id": artist_id,
                    "album_id": album_id,
                    "title": title,
                    "genre": genre,
                    "status": status,
                    "last_event_id": event_id,
                    "updated_at": ensure_utc(occurred_at),
                },
                "$setOnInsert": {"created_at": datetime.now(UTC)},
            },
            upsert=True,
        )
        await self._playback_events.update_many(
            {"track_id": track_id},
            {
                "$set": {
                    "artist_id": artist_id,
                    "album_id": album_id,
                    "track_title": title,
                    "artist_name": artist_name,
                    "track_status": status,
                }
            },
        )
        plays = await self._playback_events.count_documents({"track_id": track_id})
        await self._track_metrics.update_one(
            {"track_id": track_id},
            {
                "$set": {
                    "artist_id": artist_id,
                    "album_id": album_id,
                    "track_title": title,
                    "artist_name": artist_name,
                    "status": status,
                    "plays": plays,
                    "updated_at": datetime.now(UTC),
                },
                "$setOnInsert": {"created_at": datetime.now(UTC)},
            },
            upsert=True,
        )
        if previous_artist_id and previous_artist_id != artist_id:
            await self._rebuild_artist_metric(previous_artist_id)
        await self._rebuild_artist_metric(artist_id)
        return result.upserted_id is not None or result.modified_count > 0

    async def record_user_activity(
        self,
        event_id: str,
        user_id: str,
        occurred_at: datetime,
        activity_type: str,
    ) -> bool:
        """Persist a user activity event once."""
        result = await self._activity_events.update_one(
            {"event_id": event_id},
            {
                "$setOnInsert": {
                    "event_id": event_id,
                    "user_id": user_id,
                    "activity_type": activity_type,
                    "occurred_at": ensure_utc(occurred_at),
                    "created_at": datetime.now(UTC),
                }
            },
            upsert=True,
        )
        return result.upserted_id is not None

    async def get_artist_track_metrics(self, artist_id: str) -> list[dict[str, Any]]:
        """Return track metrics for an artist sorted by plays."""
        cursor = self._track_metrics.find({
            "artist_id": artist_id,
            "$or": [{"status": "PUBLICADO"}, {"status": {"$exists": False}}],
        }).sort("plays", -1)
        return await cursor.to_list(length=None)

    async def get_unique_listener_counts_by_track(self, artist_id: str) -> dict[str, int]:
        """Return unique listener counts grouped by track for an artist."""
        cursor = self._playback_events.aggregate(
            [
                {"$match": {"artist_id": artist_id}},
                {
                    "$group": {
                        "_id": "$track_id",
                        "listeners": {"$addToSet": "$user_id"},
                    }
                },
                {
                    "$project": {
                        "_id": 1,
                        "unique_listeners": {"$size": "$listeners"},
                    }
                },
            ]
        )
        rows = await cursor.to_list(length=None)
        return {
            str(row["_id"]): int(row.get("unique_listeners", 0))
            for row in rows
        }

    async def get_artist_daily_averages(self, artist_id: str) -> tuple[float, float]:
        """Return average unique listeners per day and average plays per day."""
        cursor = self._playback_events.aggregate(
            [
                {"$match": {"artist_id": artist_id}},
                {
                    "$group": {
                        "_id": {
                            "$dateToString": {
                                "format": "%Y-%m-%d",
                                "date": "$counted_at",
                                "timezone": "UTC",
                            }
                        },
                        "plays": {"$sum": 1},
                        "listeners": {"$addToSet": "$user_id"},
                    }
                },
                {
                    "$project": {
                        "plays": 1,
                        "unique_listeners": {"$size": "$listeners"},
                    }
                },
                {
                    "$group": {
                        "_id": None,
                        "average_daily_plays": {"$avg": "$plays"},
                        "average_daily_unique_listeners": {"$avg": "$unique_listeners"},
                    }
                },
            ]
        )
        rows = await cursor.to_list(length=1)
        if not rows:
            return (0.0, 0.0)
        row = rows[0]
        return (
            round(float(row.get("average_daily_unique_listeners", 0.0)), 2),
            round(float(row.get("average_daily_plays", 0.0)), 2),
        )

    async def get_total_play_count(self) -> int:
        """Return total global play count."""
        return await self._playback_events.count_documents({})

    async def get_activity_user_count_since(self, start_at: datetime) -> int:
        """Return unique active users since a timestamp."""
        users = await self._activity_events.distinct(
            "user_id",
            {"occurred_at": {"$gte": ensure_utc(start_at)}},
        )
        return len(users)

    async def get_top_tracks(self, limit: int) -> list[dict[str, Any]]:
        """Return globally ranked tracks."""
        cursor = self._track_metrics.find({
            "$or": [{"status": "PUBLICADO"}, {"status": {"$exists": False}}],
        }).sort("plays", -1).limit(limit)
        return await cursor.to_list(length=limit)

    async def get_top_artists(self, limit: int) -> list[dict[str, Any]]:
        """Return globally ranked artists."""
        cursor = self._track_metrics.aggregate(
            [
                {
                    "$match": {
                        "$or": [{"status": "PUBLICADO"}, {"status": {"$exists": False}}],
                        "artist_id": {"$exists": True, "$ne": None},
                    }
                },
                {
                    "$group": {
                        "_id": "$artist_id",
                        "plays": {"$sum": "$plays"},
                        "artist_name": {"$first": "$artist_name"},
                        "track_count": {"$sum": 1},
                    }
                },
                {"$match": {"track_count": {"$gt": 0}}},
                {
                    "$lookup": {
                        "from": CATALOG_ARTISTS_COLLECTION,
                        "localField": "_id",
                        "foreignField": "artist_id",
                        "as": "artist",
                    }
                },
                {"$unwind": {"path": "$artist", "preserveNullAndEmptyArrays": True}},
                {"$sort": {"plays": -1, "artist.display_name": 1, "artist_name": 1}},
                {"$limit": limit},
                {
                    "$project": {
                        "_id": 0,
                        "artist_id": "$_id",
                        "artist_name": {"$ifNull": ["$artist.display_name", "$artist_name"]},
                        "plays": "$plays",
                    }
                },
            ]
        )
        return await cursor.to_list(length=limit)

    async def get_top_albums(self, limit: int) -> list[dict[str, Any]]:
        """Return globally ranked published albums by summed track plays."""
        cursor = self._track_metrics.aggregate(
            [
                {
                    "$match": {
                        "$or": [{"status": "PUBLICADO"}, {"status": {"$exists": False}}],
                        "album_id": {"$exists": True, "$ne": None},
                    }
                },
                {
                    "$group": {
                        "_id": "$album_id",
                        "plays": {"$sum": "$plays"},
                        "artist_id": {"$first": "$artist_id"},
                    }
                },
                {
                    "$lookup": {
                        "from": CATALOG_ALBUMS_COLLECTION,
                        "localField": "_id",
                        "foreignField": "album_id",
                        "as": "album",
                    }
                },
                {"$unwind": "$album"},
                {"$match": {"album.status": "PUBLICADO"}},
                {
                    "$lookup": {
                        "from": CATALOG_ARTISTS_COLLECTION,
                        "localField": "artist_id",
                        "foreignField": "artist_id",
                        "as": "artist",
                    }
                },
                {"$unwind": {"path": "$artist", "preserveNullAndEmptyArrays": True}},
                {"$sort": {"plays": -1, "album.title": 1}},
                {"$limit": limit},
                {
                    "$project": {
                        "_id": 0,
                        "album_id": "$_id",
                        "artist_id": "$artist_id",
                        "title": "$album.title",
                        "cover_asset_id": "$album.cover_asset_id",
                        "artist_name": "$artist.display_name",
                        "plays": "$plays",
                    }
                },
            ]
        )
        return await cursor.to_list(length=limit)

    async def _resolve_artist_name(self, artist_id: str | None) -> str:
        if not artist_id:
            return "Unknown artist"
        artist = await self._catalog_artists.find_one({"artist_id": artist_id})
        if not artist:
            return "Unknown artist"
        return str(artist.get("display_name") or "Unknown artist")

    async def _rebuild_artist_metric(self, artist_id: str | None) -> None:
        if not artist_id:
            return
        cursor = self._track_metrics.aggregate(
            [
                {
                    "$match": {
                        "artist_id": artist_id,
                        "$or": [{"status": "PUBLICADO"}, {"status": {"$exists": False}}],
                    }
                },
                {"$group": {"_id": None, "plays": {"$sum": "$plays"}}},
            ]
        )
        rows = await cursor.to_list(length=1)
        plays = int(rows[0].get("plays", 0)) if rows else 0
        artist_name = await self._resolve_artist_name(artist_id)
        await self._artist_metrics.update_one(
            {"artist_id": artist_id},
            {
                "$set": {
                    "artist_id": artist_id,
                    "artist_name": artist_name,
                    "plays": plays,
                    "updated_at": datetime.now(UTC),
                },
                "$setOnInsert": {"created_at": datetime.now(UTC)},
            },
            upsert=True,
        )

    async def get_unique_listener_counts_by_artists(self, artist_ids: list[str]) -> dict[str, int]:
        """Return unique listener counts grouped by artists."""
        if not artist_ids:
            return {}
        cursor = self._playback_events.aggregate(
            [
                {"$match": {"artist_id": {"$in": artist_ids}}},
                {
                    "$group": {
                        "_id": "$artist_id",
                        "listeners": {"$addToSet": "$user_id"},
                    }
                },
                {
                    "$project": {
                        "_id": 1,
                        "unique_listeners": {"$size": "$listeners"},
                    }
                },
            ]
        )
        rows = await cursor.to_list(length=None)
        return {
            str(row["_id"]): int(row.get("unique_listeners", 0))
            for row in rows
        }

    def close(self) -> None:
        """Close the MongoDB client."""
        self._client.close()


def ensure_utc(value: datetime) -> datetime:
    """Normalize datetimes to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def optional_string(value: object) -> str | None:
    """Convert a possible value into a non-empty string."""
    if value is None:
        return None
    converted = str(value).strip()
    return converted or None
