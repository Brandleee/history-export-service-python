"""Tolerant Reader: das Event-Format darf sich additiv weiterentwickeln."""

import pytest

from app.events import InvalidEvent, SkippedEvent, parse_task_event

VALID = {
    "eventId": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "eventType": "TASK_COMPLETED",
    "taskId": "42",
    "ownerUserId": "user-a",
    "title": "Rechnung schreiben",
    "status": "DONE",
    "occurredAt": "2026-09-18T08:15:30.000Z",
}


def test_event_aus_vertrag_1_0_0_ohne_neue_felder_wird_gelesen():
    entry = parse_task_event(dict(VALID))
    assert entry.event_id == VALID["eventId"]
    assert entry.correlation_id == ""


def test_neue_optionale_felder_1_1_0_werden_uebernommen():
    entry = parse_task_event({**VALID, "schemaVersion": 1, "correlationId": "abc"})
    assert entry.correlation_id == "abc"


def test_unbekannte_zusatzfelder_werden_ignoriert():
    entry = parse_task_event({**VALID, "priority": "HIGH", "dueDate": "2026-12-01"})
    assert entry.title == "Rechnung schreiben"


def test_correlation_id_aus_amqp_property_als_fallback():
    assert parse_task_event(dict(VALID), fallback_correlation_id="from-header").correlation_id == "from-header"


def test_unbekannte_schema_version_wird_uebersprungen():
    assert isinstance(parse_task_event({**VALID, "schemaVersion": 2}), SkippedEvent)


def test_unbekannter_event_type_wird_uebersprungen():
    assert isinstance(parse_task_event({**VALID, "eventType": "TASK_ARCHIVED"}), SkippedEvent)


@pytest.mark.parametrize("field", ["eventId", "ownerUserId", "occurredAt"])
def test_fehlendes_pflichtfeld_ist_ungueltig(field):
    payload = dict(VALID)
    del payload[field]
    with pytest.raises(InvalidEvent):
        parse_task_event(payload)


def test_kein_objekt_ist_ungueltig():
    with pytest.raises(InvalidEvent):
        parse_task_event(["kein", "objekt"])
