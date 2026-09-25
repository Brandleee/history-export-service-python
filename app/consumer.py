"""Konsumiert Task-Events vom Fanout-Exchange "task-events" per AMQP (pika).

Der Task-Service (Delphi) published dieselben Events per STOMP - RabbitMQ
vermittelt zwischen beiden Protokollen auf demselben Exchange, siehe
../../contracts/README.md ("Bewusste Interoperabilitaets-Demo").

Vertrag (Event-Format): ../../contracts/asyncapi/task-events.yaml

Laeuft in einem eigenen Hintergrund-Thread (siehe main.py), analog zum
Fanout-Consumer-Beispiel aus dem M321-Demo-Projekt: eine eigene, exklusive
Queue pro laufender Instanz - startet der Service neu, verpasst er alles,
was waehrend des Unterbruchs published wurde (bewusstes Fanout-Verhalten,
kein Nachliefern wie bei einer Work-Queue).
"""

import json
import logging
import os
import threading
import time

import pika

from app.store import HistoryEntry, store

logger = logging.getLogger("consumer")

RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = int(os.environ.get("RABBITMQ_PORT", "5672"))
EXCHANGE_NAME = "task-events"


def _handle_message(channel, method, properties, body: bytes) -> None:
    try:
        payload = json.loads(body.decode("utf-8"))
        entry = HistoryEntry(
            event_id=payload["eventId"],
            event_type=payload["eventType"],
            task_id=payload["taskId"],
            owner_user_id=payload["ownerUserId"],
            title=payload["title"],
            status=payload["status"],
            occurred_at=payload["occurredAt"],
        )
        store.add(entry)
        logger.info("Event verarbeitet: %s %s (Task %s)", entry.event_type, entry.event_id, entry.task_id)
    except (KeyError, json.JSONDecodeError) as exc:
        logger.warning("Ungueltiges Event ignoriert: %s (%s)", body, exc)
    finally:
        channel.basic_ack(delivery_tag=method.delivery_tag)


def _run_forever() -> None:
    while True:
        try:
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
            )
            channel = connection.channel()
            channel.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="fanout", durable=True)
            queue = channel.queue_declare(queue="", exclusive=True)
            queue_name = queue.method.queue
            channel.queue_bind(exchange=EXCHANGE_NAME, queue=queue_name)

            logger.info("Warte auf Task-Events (eigene Queue: %s) ...", queue_name)
            channel.basic_consume(queue=queue_name, on_message_callback=_handle_message)
            channel.start_consuming()
        except pika.exceptions.AMQPConnectionError as exc:
            logger.warning("RabbitMQ nicht erreichbar (%s), erneuter Versuch in 3s ...", exc)
            time.sleep(3)


def start_consumer_thread() -> threading.Thread:
    thread = threading.Thread(target=_run_forever, name="task-events-consumer", daemon=True)
    thread.start()
    return thread
