import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import pika
from pika.adapters.blocking_connection import BlockingChannel
from pika.exceptions import AMQPError

from app.analytics.service import AnalyticsService
from app.events.signer import is_signature_valid

logger = logging.getLogger(__name__)

STREAMING_EXCHANGE = "streaming.events"
TRACK_PLAYBACK_COUNTED_ROUTING_KEY = "track.playback.counted"
IDENTITY_EXCHANGE = "identity.events"
USER_LOGGED_IN_ROUTING_KEY = "user.logged-in"
CATALOG_EXCHANGE = "catalog.events"
CATALOG_EVENTS_ROUTING_KEY = "#"
SIGNATURE_HEADER = "X-Event-Signature"
PREFETCH_COUNT = 10
RECONNECT_DELAY_SECONDS = 5


class AnalyticsEventConsumer:
    """Background RabbitMQ consumer for analytics projections."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        signing_secret: str,
        playback_queue: str,
        login_queue: str,
        catalog_queue: str,
        analytics_service: AnalyticsService,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Create a RabbitMQ consumer."""
        if not signing_secret.strip():
            raise ValueError("EVENT_SIGNING_SECRET must be configured.")
        if not password.strip():
            raise ValueError("RABBITMQ_DEFAULT_PASS must be configured.")

        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._signing_secret = signing_secret
        self._playback_queue = playback_queue
        self._login_queue = login_queue
        self._catalog_queue = catalog_queue
        self._analytics_service = analytics_service
        self._loop = loop
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._connection: pika.BlockingConnection | None = None

    def start(self) -> None:
        """Start consuming RabbitMQ events in a background thread."""
        if self._thread and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="analytics-rabbitmq-consumer",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the consumer thread and close the RabbitMQ connection."""
        self._stop_event.set()
        connection = self._connection
        if connection and connection.is_open:
            try:
                connection.add_callback_threadsafe(connection.close)
            except AMQPError:
                logger.warning("RabbitMQ connection was already closing.")

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._consume_once()
            except (AMQPError, OSError) as exc:
                if not self._stop_event.is_set():
                    logger.error("Analytics RabbitMQ consumer failed: %s", exc, exc_info=True)
                    time.sleep(RECONNECT_DELAY_SECONDS)
            except Exception as exc:
                if not self._stop_event.is_set():
                    logger.error("Unexpected analytics consumer failure: %s", exc, exc_info=True)
                    time.sleep(RECONNECT_DELAY_SECONDS)

    def _consume_once(self) -> None:
        credentials = pika.PlainCredentials(self._username, self._password)
        parameters = pika.ConnectionParameters(
            host=self._host,
            port=self._port,
            credentials=credentials,
            heartbeat=30,
            blocked_connection_timeout=5,
        )

        with pika.BlockingConnection(parameters) as connection:
            self._connection = connection
            channel = connection.channel()
            channel.basic_qos(prefetch_count=PREFETCH_COUNT)
            self._declare_topology(channel)

            channel.basic_consume(
                queue=self._playback_queue,
                on_message_callback=self._build_callback(
                    self._analytics_service.record_track_playback
                ),
            )
            channel.basic_consume(
                queue=self._login_queue,
                on_message_callback=self._build_callback(
                    self._analytics_service.record_user_login
                ),
            )
            channel.basic_consume(
                queue=self._catalog_queue,
                on_message_callback=self._build_callback(
                    self._analytics_service.record_catalog_snapshot
                ),
            )

            logger.info("Analytics RabbitMQ consumer connected.")
            while not self._stop_event.is_set() and connection.is_open:
                connection.process_data_events(time_limit=1)

    def _declare_topology(self, channel: BlockingChannel) -> None:
        channel.exchange_declare(
            exchange=STREAMING_EXCHANGE,
            exchange_type="topic",
            durable=True,
        )
        channel.queue_declare(queue=self._playback_queue, durable=True)
        channel.queue_bind(
            queue=self._playback_queue,
            exchange=STREAMING_EXCHANGE,
            routing_key=TRACK_PLAYBACK_COUNTED_ROUTING_KEY,
        )

        channel.exchange_declare(
            exchange=IDENTITY_EXCHANGE,
            exchange_type="topic",
            durable=True,
        )
        channel.queue_declare(queue=self._login_queue, durable=True)
        channel.queue_bind(
            queue=self._login_queue,
            exchange=IDENTITY_EXCHANGE,
            routing_key=USER_LOGGED_IN_ROUTING_KEY,
        )

        channel.exchange_declare(
            exchange=CATALOG_EXCHANGE,
            exchange_type="topic",
            durable=True,
        )
        channel.queue_declare(queue=self._catalog_queue, durable=True)
        channel.queue_bind(
            queue=self._catalog_queue,
            exchange=CATALOG_EXCHANGE,
            routing_key=CATALOG_EVENTS_ROUTING_KEY,
        )

    def _build_callback(
        self,
        handler: Callable[[dict[str, Any]], Any],
    ) -> Callable[[BlockingChannel, Any, Any, Any], None]:
        def callback(
            channel: BlockingChannel,
            _method: Any,
            properties: Any,
            body: bytes,
        ) -> None:
            delivery_tag = _method.delivery_tag
            try:
                payload_text = body.decode("utf-8")
                signature = get_signature_header(properties)
                if not is_signature_valid(payload_text, signature, self._signing_secret):
                    logger.warning("Rejected analytics event with invalid signature.")
                    channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
                    return

                payload = json.loads(payload_text)
                if not isinstance(payload, dict):
                    raise ValueError("Event payload must be an object.")

                future = asyncio.run_coroutine_threadsafe(
                    handler(payload),
                    self._loop,
                )
                future.result(timeout=30)
                channel.basic_ack(delivery_tag=delivery_tag)
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("Rejected malformed analytics event: %s", exc)
                channel.basic_nack(delivery_tag=delivery_tag, requeue=False)
            except Exception as exc:
                logger.error("Failed to process analytics event: %s", exc, exc_info=True)
                channel.basic_nack(delivery_tag=delivery_tag, requeue=True)

        return callback


def get_signature_header(properties: Any) -> str | None:
    """Read RabbitMQ signature headers regardless of client casing behavior."""
    headers = getattr(properties, "headers", None)
    if not isinstance(headers, dict):
        return None
    value = headers.get(SIGNATURE_HEADER) or headers.get(SIGNATURE_HEADER.lower())
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if value is None:
        return None
    return str(value)
