import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.analytics.repository import MongoAnalyticsRepository, ensure_utc, optional_string
from app.auth.jwt_validator import JwtValidator
from app.auth.models import UserRole, normalize_role
from app.errors import AppError
from app.events import consumer as consumer_module
from app.events.consumer import AnalyticsEventConsumer, get_signature_header
from app.events.signer import compute_hmac_base64, is_signature_valid


class UpdateResult:
    def __init__(self, upserted_id=None, modified_count=0):
        self.upserted_id = upserted_id
        self.modified_count = modified_count


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sort_args = None
        self.skip_count = None
        self.limit_count = None

    def sort(self, *args):
        self.sort_args = args
        return self

    def skip(self, count):
        self.skip_count = count
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    async def to_list(self, length):
        if length is None:
            return list(self.rows)
        return list(self.rows)[:length]


class FakeCollection:
    def __init__(self):
        self.indexes = []
        self.update_one_calls = []
        self.update_many_calls = []
        self.aggregate_calls = []
        self.find_one_result = None
        self.update_one_result = UpdateResult("inserted", 0)
        self.update_many_result = UpdateResult(None, 1)
        self.count_result = 0
        self.distinct_result = []
        self.aggregate_rows = []
        self.find_rows = []

    async def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))

    async def update_one(self, *args, **kwargs):
        self.update_one_calls.append((args, kwargs))
        return self.update_one_result

    async def update_many(self, *args, **kwargs):
        self.update_many_calls.append((args, kwargs))
        return self.update_many_result

    async def find_one(self, *_args, **_kwargs):
        return self.find_one_result

    async def count_documents(self, *_args, **_kwargs):
        return self.count_result

    async def distinct(self, *_args, **_kwargs):
        return self.distinct_result

    def aggregate(self, *args, **_kwargs):
        self.aggregate_calls.append(args)
        return FakeCursor(self.aggregate_rows)

    def find(self, *_args, **_kwargs):
        return FakeCursor(self.find_rows)


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        self.collections.setdefault(name, FakeCollection())
        return self.collections[name]


class FakeClient:
    def __init__(self):
        self.database = FakeDatabase()
        self.closed = False

    def __getitem__(self, _name):
        return self.database

    def close(self):
        self.closed = True


class FakeChannel:
    def __init__(self):
        self.exchanges = []
        self.queues = []
        self.bindings = []
        self.acks = []
        self.nacks = []

    def exchange_declare(self, **kwargs):
        self.exchanges.append(kwargs)

    def queue_declare(self, **kwargs):
        self.queues.append(kwargs)

    def queue_bind(self, **kwargs):
        self.bindings.append(kwargs)

    def basic_ack(self, **kwargs):
        self.acks.append(kwargs)

    def basic_nack(self, **kwargs):
        self.nacks.append(kwargs)


def build_repository():
    client = FakeClient()
    return MongoAnalyticsRepository(client, "analytics"), client


def build_consumer():
    loop = asyncio.new_event_loop()
    service = SimpleNamespace(
        record_track_playback=lambda payload: payload,
        record_user_login=lambda payload: payload,
        record_catalog_snapshot=lambda payload: payload,
    )
    consumer = AnalyticsEventConsumer(
        host="rabbitmq",
        port=5672,
        username="guest",
        password="guest",
        signing_secret="secret",
        playback_queue="analytics.playback",
        login_queue="analytics.login",
        catalog_queue="analytics.catalog",
        analytics_service=service,
        loop=loop,
    )
    return consumer, loop


def test_jwt_validator_rejects_missing_configuration() -> None:
    with pytest.raises(ValueError):
        JwtValidator("", "issuer")

    with pytest.raises(ValueError):
        JwtValidator("http://jwks", " ")


def test_jwt_validator_extracts_bearer_token() -> None:
    assert JwtValidator.extract_bearer_token("Bearer token-1") == "token-1"

    with pytest.raises(AppError):
        JwtValidator.extract_bearer_token(None)

    with pytest.raises(AppError):
        JwtValidator.extract_bearer_token("Basic token-1")


def test_jwt_validator_maps_valid_token(monkeypatch) -> None:
    validator = JwtValidator("http://jwks", "issuer")
    monkeypatch.setattr("app.auth.jwt_validator.jwt.get_unverified_header", lambda _token: {
        "alg": "RS256",
        "kid": "kid-1",
    })
    monkeypatch.setattr(validator, "_get_signing_key", lambda _kid: "public-key")
    monkeypatch.setattr(validator, "_decode_token", lambda _token, _key: {
        "sub": " user-1 ",
        "role": "ROLE_ADMIN",
    })
    monkeypatch.setattr(validator, "_validate_account_state", lambda _token: None)

    user = validator.validate_authorization_header("Bearer token-1")

    assert user.subject == "user-1"
    assert user.role == UserRole.ADMIN


def test_jwt_validator_rejects_bad_header_claims(monkeypatch) -> None:
    validator = JwtValidator("http://jwks", "issuer")

    monkeypatch.setattr("app.auth.jwt_validator.jwt.get_unverified_header", lambda _token: {
        "alg": "HS256",
        "kid": "kid-1",
    })
    with pytest.raises(AppError):
        validator.validate_token("token-1")

    monkeypatch.setattr("app.auth.jwt_validator.jwt.get_unverified_header", lambda _token: {
        "alg": "RS256",
    })
    with pytest.raises(AppError):
        validator.validate_token("token-1")


def test_jwt_validator_rejects_invalid_payload(monkeypatch) -> None:
    validator = JwtValidator("http://jwks", "issuer")
    monkeypatch.setattr("app.auth.jwt_validator.jwt.get_unverified_header", lambda _token: {
        "alg": "RS256",
        "kid": "kid-1",
    })
    monkeypatch.setattr(validator, "_get_signing_key", lambda _kid: "public-key")

    monkeypatch.setattr(validator, "_decode_token", lambda _token, _key: {"role": "ADMIN"})
    with pytest.raises(AppError):
        validator.validate_token("token-1")

    monkeypatch.setattr(validator, "_decode_token", lambda _token, _key: {
        "sub": "user-1",
        "role": "UNKNOWN",
    })
    with pytest.raises(AppError):
        validator.validate_token("token-1")


def test_jwt_validator_rejects_suspended_account(monkeypatch) -> None:
    validator = JwtValidator("http://jwks", "issuer")
    monkeypatch.setattr("app.auth.jwt_validator.jwt.get_unverified_header", lambda _token: {
        "alg": "RS256",
        "kid": "kid-1",
    })
    monkeypatch.setattr(validator, "_get_signing_key", lambda _kid: "public-key")
    monkeypatch.setattr(validator, "_decode_token", lambda _token, _key: {
        "sub": "user-1",
        "role": "ADMIN",
    })

    def reject_suspended(_token: str) -> None:
        raise AppError(
            403,
            "AccountBannedException",
            "La cuenta se encuentra suspendida.",
            {"code": "ACCOUNT_BANNED"},
        )

    monkeypatch.setattr(validator, "_validate_account_state", reject_suspended)

    with pytest.raises(AppError) as exc_info:
        validator.validate_token("token-1")

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "AccountBannedException"


def test_jwt_validator_refreshes_and_caches_jwks(monkeypatch) -> None:
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"keys": [{"kid": "kid-1", "kty": "RSA"}]}

    validator = JwtValidator("http://jwks", "issuer")
    monkeypatch.setattr("app.auth.jwt_validator.httpx.get", lambda *_args, **_kwargs: Response())

    jwks = validator._get_cached_jwks()

    assert validator._get_cached_jwks() is jwks
    assert JwtValidator._find_key(jwks, "kid-1") == {"kid": "kid-1", "kty": "RSA"}
    assert JwtValidator._find_key(jwks, "missing") is None


def test_normalize_role_accepts_role_prefix() -> None:
    assert normalize_role(" role_artist ") == UserRole.ARTIST

    with pytest.raises(ValueError):
        normalize_role("owner")


def test_repository_records_playback_and_updates_metrics() -> None:
    async def run() -> None:
        repository, client = build_repository()
        collections = client.database.collections
        collections["catalog_tracks"].find_one_result = {
            "artist_id": "artist-1",
            "title": "Luna",
        }
        collections["catalog_artists"].find_one_result = {"display_name": "Ada"}

        recorded = await repository.record_playback(
            event_id="event-1",
            user_id="user-1",
            track_id="track-1",
            counted_at=datetime(2026, 5, 20, 12, 0),
            position_seconds=31,
        )

        assert recorded is True
        assert collections["playback_events"].update_one_calls[0][0][0] == {"event_id": "event-1"}
        assert collections["activity_events"].update_one_calls[0][0][0] == {"event_id": "playback:event-1"}
        assert collections["track_metrics"].update_one_calls[0][0][0] == {"track_id": "track-1"}
        assert collections["artist_metrics"].update_one_calls[0][0][0] == {"artist_id": "artist-1"}

    asyncio.run(run())


def test_repository_skips_duplicate_playback_event() -> None:
    async def run() -> None:
        repository, client = build_repository()
        collections = client.database.collections
        collections["playback_events"].update_one_result = UpdateResult(None, 0)

        recorded = await repository.record_playback(
            event_id="event-1",
            user_id="user-1",
            track_id="track-1",
            counted_at=datetime.now(UTC),
            position_seconds=0,
        )

        assert recorded is False
        assert collections["track_metrics"].update_one_calls == []

    asyncio.run(run())


def test_repository_upserts_snapshots_and_query_helpers() -> None:
    async def run() -> None:
        repository, client = build_repository()
        collections = client.database.collections
        collections["catalog_artists"].find_one_result = {"display_name": "Ada"}
        collections["catalog_tracks"].find_one_result = {"artist_id": "artist-old"}
        collections["playback_events"].count_result = 4
        collections["playback_events"].aggregate_rows = [{"_id": "track-1", "unique_listeners": 3}]
        collections["track_metrics"].find_rows = [{"track_id": "track-1", "plays": 7}]
        collections["track_metrics"].aggregate_rows = [{"artist_id": "artist-1", "plays": 9}]
        collections["activity_events"].distinct_result = ["u1", "u2"]

        assert await repository.upsert_artist_snapshot("event-a", "artist-1", "Ada", datetime.now(UTC))
        assert await repository.upsert_album_snapshot(
            "event-b",
            "album-1",
            "artist-1",
            "LP",
            None,
            "PUBLICADO",
            datetime.now(UTC),
        )
        assert await repository.upsert_track_snapshot(
            "event-t",
            "track-1",
            "artist-1",
            None,
            "Luna",
            "Rock",
            "PUBLICADO",
            datetime.now(UTC),
        )

        assert await repository.get_artist_track_metrics("artist-1") == [{"track_id": "track-1", "plays": 7}]
        assert await repository.get_unique_listener_counts_by_track("artist-1") == {"track-1": 3}
        assert await repository.get_total_play_count() == 4
        assert await repository.get_activity_user_count_since(datetime.now(UTC)) == 2
        assert await repository.get_top_tracks(1) == [{"track_id": "track-1", "plays": 7}]
        assert await repository.get_top_artists(1) == [{"artist_id": "artist-1", "plays": 9}]
        assert await repository.get_unique_listener_counts_by_artists([]) == {}

    asyncio.run(run())


def test_repository_top_albums_accepts_legacy_albums_without_status() -> None:
    async def run() -> None:
        repository, client = build_repository()
        collections = client.database.collections
        collections["track_metrics"].aggregate_rows = [
            {"album_id": "album-1", "artist_id": "artist-1", "title": "Noches", "plays": 8}
        ]

        await repository.get_top_albums(10)

        aggregate_pipeline = collections["track_metrics"].aggregate_calls[0][0]
        legacy_album_match = aggregate_pipeline[4]["$match"]

        assert legacy_album_match == {
            "$or": [
                {"album.status": "PUBLICADO"},
                {"album.status": {"$exists": False}},
            ]
        }

    asyncio.run(run())


def test_repository_daily_averages_and_indexes() -> None:
    async def run() -> None:
        repository, client = build_repository()
        collections = client.database.collections

        assert await repository.get_artist_daily_averages("artist-1") == (0.0, 0.0)

        collections["playback_events"].aggregate_rows = [
            {"average_daily_unique_listeners": 2.345, "average_daily_plays": 7.891}
        ]
        assert await repository.get_artist_daily_averages("artist-1") == (2.35, 7.89)

        await repository.ensure_indexes()
        assert collections["playback_events"].indexes
        assert collections["catalog_tracks"].indexes
        repository.close()
        assert client.closed is True

    asyncio.run(run())


def test_repository_helpers_normalize_values() -> None:
    naive = datetime(2026, 5, 20, 12, 0)
    assert ensure_utc(naive).tzinfo == UTC
    assert optional_string("  value  ") == "value"
    assert optional_string("   ") is None
    assert optional_string(None) is None


def test_consumer_requires_runtime_secrets() -> None:
    consumer, loop = build_consumer()
    loop.close()
    assert consumer is not None

    with pytest.raises(ValueError):
        AnalyticsEventConsumer(
            "rabbitmq",
            5672,
            "guest",
            "guest",
            " ",
            "playback",
            "login",
            "catalog",
            SimpleNamespace(),
            asyncio.new_event_loop(),
        )


def test_consumer_declares_topology() -> None:
    consumer, loop = build_consumer()
    channel = FakeChannel()

    consumer._declare_topology(channel)

    assert {item["exchange"] for item in channel.exchanges} == {
        "streaming.events",
        "identity.events",
        "catalog.events",
    }
    assert len(channel.bindings) == 3
    loop.close()


def test_consumer_callback_acknowledges_valid_event(monkeypatch) -> None:
    consumer, loop = build_consumer()
    payload_text = '{"eventId":"event-1"}'
    signature = compute_hmac_base64(payload_text, "secret")
    channel = FakeChannel()
    handled = []

    async def handler(payload):
        handled.append(payload)

    class Future:
        def result(self, timeout):
            assert timeout == 30
            return None

    def run_coroutine_threadsafe(coro, _loop):
        loop.run_until_complete(coro)
        return Future()

    monkeypatch.setattr(consumer_module.asyncio, "run_coroutine_threadsafe", run_coroutine_threadsafe)
    callback = consumer._build_callback(handler)
    callback(
        channel,
        SimpleNamespace(delivery_tag=42),
        SimpleNamespace(headers={"X-Event-Signature": signature}),
        payload_text.encode("utf-8"),
    )

    assert handled == [{"eventId": "event-1"}]
    assert channel.acks == [{"delivery_tag": 42}]
    loop.close()


def test_consumer_callback_rejects_bad_events(monkeypatch) -> None:
    consumer, loop = build_consumer()
    channel = FakeChannel()

    async def handler(_payload):
        raise RuntimeError("boom")

    class FailingFuture:
        def result(self, _timeout):
            raise RuntimeError("boom")

    def failing_run_coroutine_threadsafe(coro, _loop):
        coro.close()
        return FailingFuture()

    monkeypatch.setattr(
        consumer_module.asyncio,
        "run_coroutine_threadsafe",
        failing_run_coroutine_threadsafe,
    )
    callback = consumer._build_callback(handler)
    callback(
        channel,
        SimpleNamespace(delivery_tag=1),
        SimpleNamespace(headers={"X-Event-Signature": "bad"}),
        b'{"eventId":"event-1"}',
    )
    callback(
        channel,
        SimpleNamespace(delivery_tag=2),
        SimpleNamespace(headers={"X-Event-Signature": compute_hmac_base64("[]", "secret")}),
        b"[]",
    )
    payload = '{"eventId":"event-2"}'
    callback(
        channel,
        SimpleNamespace(delivery_tag=3),
        SimpleNamespace(headers={"X-Event-Signature": compute_hmac_base64(payload, "secret")}),
        payload.encode("utf-8"),
    )

    assert channel.nacks == [
        {"delivery_tag": 1, "requeue": False},
        {"delivery_tag": 2, "requeue": False},
        {"delivery_tag": 3, "requeue": True},
    ]
    loop.close()


def test_signature_helpers_and_headers() -> None:
    signature = compute_hmac_base64("{}", "secret")
    assert is_signature_valid("{}", signature, "secret")
    assert not is_signature_valid("{}", None, "secret")
    assert get_signature_header(SimpleNamespace(headers={"x-event-signature": b"abc"})) == "abc"
    assert get_signature_header(SimpleNamespace(headers=None)) is None
