"""Liest Task-Events nach dem Tolerant-Reader-Prinzip.

Vertrag: contracts/asyncapi/task-events.yaml (Version 1.1.0), Regeln fuer
Weiterentwicklung: contracts/EVOLUTION.md.

- Unbekannte Felder werden ignoriert (neue optionale Felder eines
  neueren Publishers brechen diesen Consumer nicht).
- Fehlende optionale Felder bekommen Defaults (schemaVersion fehlt -> 1,
  correlationId fehlt -> "").
- Events mit unbekannter schemaVersion oder unbekanntem eventType werden
  nicht verarbeitet, aber auch nicht als Fehler behandelt (siehe consumer.py).
"""

from dataclasses import dataclass

from app.store import HistoryEntry

SUPPORTED_SCHEMA_VERSIONS = {1}
KNOWN_EVENT_TYPES = {"TASK_CREATED", "TASK_UPDATED", "TASK_COMPLETED", "TASK_DELETED"}
_REQUIRED = ("eventId", "eventType", "taskId", "ownerUserId", "title", "status", "occurredAt")


class InvalidEvent(ValueError):
    """Event verletzt den Vertrag (Pflichtfeld fehlt, falscher Typ)."""


@dataclass(frozen=True)
class SkippedEvent:
    """Gueltiges, aber fuer diesen Consumer nicht relevantes/verstaendliches Event."""

    reason: str


def parse_task_event(payload: object, fallback_correlation_id: str = "") -> HistoryEntry | SkippedEvent:
    if not isinstance(payload, dict):
        raise InvalidEvent("Event ist kein JSON-Objekt")

    schema_version = payload.get("schemaVersion", 1)
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        return SkippedEvent(f"schemaVersion {schema_version!r} wird nicht unterstuetzt")

    missing = [field for field in _REQUIRED if not isinstance(payload.get(field), str)]
    if missing:
        raise InvalidEvent(f"Pflichtfelder fehlen oder sind keine Strings: {', '.join(missing)}")

    if payload["eventType"] not in KNOWN_EVENT_TYPES:
        return SkippedEvent(f"unbekannter eventType {payload['eventType']!r}")

    correlation = payload.get("correlationId")
    return HistoryEntry(
        event_id=payload["eventId"],
        event_type=payload["eventType"],
        task_id=payload["taskId"],
        owner_user_id=payload["ownerUserId"],
        title=payload["title"],
        status=payload["status"],
        occurred_at=payload["occurredAt"],
        correlation_id=correlation if isinstance(correlation, str) else fallback_correlation_id,
    )
