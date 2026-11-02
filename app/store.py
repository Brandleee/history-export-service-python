"""Thread-sicherer In-Memory-Speicher fuer History-Eintraege.

Bewusst so einfach wie moeglich gehalten (TODO fuer die Weiterentwicklung:
durch echte Persistenz ersetzen, z.B. SQLite/Postgres) - der Fokus dieses
Grundgerueests liegt auf dem asynchronen Event-Fluss, nicht auf Persistenz.
"""

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class HistoryEntry:
    event_id: str
    event_type: str
    task_id: str
    owner_user_id: str
    title: str
    status: str
    occurred_at: str


class HistoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[HistoryEntry] = []

    def add(self, entry: HistoryEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def list_for_user(self, owner_user_id: str) -> list[HistoryEntry]:
        with self._lock:
            matches = [e for e in self._entries if e.owner_user_id == owner_user_id]
        # Neueste zuerst - passend zum Vertrag (contracts/openapi/history-export-service.yaml).
        # Nach echtem Zeitpunkt sortieren, nicht als Text - die Services schicken
        # unterschiedliche Zeitzonen (Delphi +02:00, Python +00:00).
        return sorted(matches, key=lambda e: _parse_time(e.occurred_at), reverse=True)


store = HistoryStore()
