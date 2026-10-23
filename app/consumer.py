"""Konsumiert Task-Events vom Fanout-Exchange "task-events" per AMQP (pika).

Der Task-Service (Delphi) published dieselben Events per STOMP - RabbitMQ
vermittelt zwischen beiden Protokollen auf demselben Exchange, siehe
contracts/README.md ("Bewusste Interoperabilitaets-Demo").

Vertrag (Event-Format): contracts/asyncapi/task-events.yaml (1.1.0)

- Durable, benannte Queue "history-export-service.task-events": Events
  bleiben liegen, waehrend dieser Service ausgefallen ist oder neu deployed
  wird, und werden danach nachgeliefert.
  Ausnahme: ohne Datenbank (lokaler In-Memory-Modus) nutzt die Instanz eine
  eigene, temporaere Queue - sonst wuerde sie als Competing Consumer den
  echten (persistenten) Instanzen Events wegnehmen, die dann nur in ihrem
  fluechtigen Speicher landen.
- Alle Instanzen dieses Services lesen aus derselben Queue (Competing
  Consumers) - so skaliert der Service horizontal.
- Ack erst NACH dem Speichern. Doppelte Zustellungen (at-least-once) werden
  im Store ueber eventId erkannt und verworfen (Idempotenz).
- Ungueltige oder unverstaendliche Events werden geloggt und bestaetigt,
  damit sie die Queue nicht blockieren (Tolerant Reader, siehe events.py).
- Ist die DB voruebergehend weg, wird das Event NICHT bestaetigt, sondern
  zurueck in die Queue gestellt und spaeter erneut versucht.
"""

import json
import logging
import os
import threading
import time

import pika
from pika.adapters.blocking_connection import BlockingChannel

from app.events import InvalidEvent, SkippedEvent, parse_task_event
from app.observability import correlation_id
from app.store import HistoryStore

logger = logging.getLogger("consumer")

RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.environ.get("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.environ.get("RABBITMQ_USER", "guest")
RABBITMQ_PASS = os.environ.get("RABBITMQ_PASS", "guest")
EXCHANGE_NAME = "task-events"
DURABLE_QUEUE_NAME = "history-export-service.task-events"
PREFETCH = 10


class TaskEventConsumer:
    def __init__(self, store: HistoryStore, queue_name: str | None = DURABLE_QUEUE_NAME) -> None:
        """queue_name=None: exklusive, temporaere Queue nur fuer diese Instanz."""
        self._store = store
        self._queue_name = queue_name
        self._stopping = threading.Event()
        self._connection: pika.BlockingConnection | None = None
        self._thread = threading.Thread(target=self._run_forever, name="task-events-consumer", daemon=True)
        self.connected = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Sauber beenden: laufende Nachricht fertig verarbeiten, Rest bleibt in der Queue."""
        self._stopping.set()
        connection = self._connection
        if connection is not None and connection.is_open:
            try:
                connection.add_callback_threadsafe(self._close)
            except Exception:
                pass
        self._thread.join(timeout=5)

    def _close(self) -> None:
        if self._connection is not None and self._connection.is_open:
            self._connection.close()

    def handle_message(self, channel: BlockingChannel, method, properties, body: bytes) -> None:
        token = correlation_id.set(getattr(properties, "correlation_id", None) or "")
        try:
            try:
                payload = json.loads(body.decode("utf-8"))
                result = parse_task_event(payload, fallback_correlation_id=correlation_id.get())
            except (UnicodeDecodeError, json.JSONDecodeError, InvalidEvent) as exc:
                logger.warning("Ungueltiges Event verworfen: %s", exc,
                               extra={"body": body[:500].decode("utf-8", "replace")})
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            if isinstance(result, SkippedEvent):
                logger.warning("Event uebersprungen: %s", result.reason, extra={"eventId": payload.get("eventId")})
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            if result.correlation_id:
                correlation_id.set(result.correlation_id)
            try:
                is_new = self._store.add(result)
            except Exception:
                # DB voruebergehend weg: nicht bestaetigen, spaeter erneut versuchen.
                logger.exception("Event konnte nicht gespeichert werden, wird erneut zugestellt",
                                 extra={"eventId": result.event_id})
                time.sleep(1)
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                return

            channel.basic_ack(delivery_tag=method.delivery_tag)
            if is_new:
                logger.info("Event verarbeitet: %s", result.event_type,
                            extra={"eventId": result.event_id, "taskId": result.task_id,
                                   "redelivered": method.redelivered})
            else:
                logger.info("Duplikat ignoriert (Idempotenz)", extra={"eventId": result.event_id})
        finally:
            correlation_id.reset(token)

    def _run_forever(self) -> None:
        backoff = 1.0
        while not self._stopping.is_set():
            try:
                self._connection = pika.BlockingConnection(
                    pika.ConnectionParameters(
                        host=RABBITMQ_HOST,
                        port=RABBITMQ_PORT,
                        credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS),
                        heartbeat=30,
                        connection_attempts=1,
                        socket_timeout=5,
                    )
                )
                channel = self._connection.channel()
                channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="fanout", durable=True)
                if self._queue_name:
                    queue = channel.queue_declare(queue=self._queue_name, durable=True).method.queue
                else:
                    queue = channel.queue_declare(queue="", exclusive=True).method.queue
                channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue)
                channel.basic_qos(prefetch_count=PREFETCH)
                channel.basic_consume(queue=queue, on_message_callback=self.handle_message)

                self.connected = True
                backoff = 1.0
                logger.info("Warte auf Task-Events", extra={"queue": queue})
                channel.start_consuming()
            except Exception as exc:
                if self._stopping.is_set():
                    break
                logger.warning("RabbitMQ-Verbindung verloren/nicht moeglich (%s), neuer Versuch in %.0fs", exc, backoff)
                self._stopping.wait(backoff)
                backoff = min(backoff * 2, 30.0)
            finally:
                self.connected = False
        logger.info("Consumer beendet")
