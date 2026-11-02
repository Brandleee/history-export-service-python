"""Consumer-Verhalten: Ack/Nack, Idempotenz, Poison-Messages."""

import json
from types import SimpleNamespace

from app.consumer import TaskEventConsumer
from app.store import InMemoryHistoryStore

EVENT = {
    "eventId": "e-1", "eventType": "TASK_CREATED", "taskId": "1", "ownerUserId": "user-a",
    "title": "A", "status": "OPEN", "occurredAt": "2026-10-01T10:00:00.000Z", "correlationId": "c-1",
}


class FakeChannel:
    def __init__(self):
        self.acked, self.nacked = [], []

    def basic_ack(self, delivery_tag):
        self.acked.append(delivery_tag)

    def basic_nack(self, delivery_tag, requeue):
        self.nacked.append((delivery_tag, requeue))


def deliver(consumer, channel, body, tag=1, redelivered=False):
    method = SimpleNamespace(delivery_tag=tag, redelivered=redelivered)
    consumer.handle_message(channel, method, SimpleNamespace(correlation_id=None), body)


def test_event_wird_gespeichert_und_bestaetigt():
    store, channel = InMemoryHistoryStore(), FakeChannel()
    deliver(TaskEventConsumer(store), channel, json.dumps(EVENT).encode())
    assert channel.acked == [1]
    assert store.list_for_user("user-a")[0].correlation_id == "c-1"


def test_doppelte_zustellung_wird_bestaetigt_aber_nicht_doppelt_gespeichert():
    store, channel = InMemoryHistoryStore(), FakeChannel()
    consumer = TaskEventConsumer(store)
    deliver(consumer, channel, json.dumps(EVENT).encode(), tag=1)
    deliver(consumer, channel, json.dumps(EVENT).encode(), tag=2, redelivered=True)
    assert channel.acked == [1, 2]
    assert len(store.list_for_user("user-a")) == 1


def test_kaputtes_json_blockiert_die_queue_nicht():
    channel = FakeChannel()
    deliver(TaskEventConsumer(InMemoryHistoryStore()), channel, b"{kein json")
    assert channel.acked == [1] and channel.nacked == []


def test_db_ausfall_fuehrt_zu_requeue(monkeypatch):
    store, channel = InMemoryHistoryStore(), FakeChannel()

    def broken_add(entry):
        raise OSError("db weg")

    monkeypatch.setattr(store, "add", broken_add)
    monkeypatch.setattr("app.consumer.time.sleep", lambda seconds: None)
    deliver(TaskEventConsumer(store), channel, json.dumps(EVENT).encode())
    assert channel.acked == [] and channel.nacked == [(1, True)]
