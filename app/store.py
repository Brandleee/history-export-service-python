"""Speicher fuer History-Eintraege - idempotent ueber eventId.

Zwei Implementierungen mit derselben Schnittstelle:

- PostgresHistoryStore (wenn DATABASE_URL gesetzt ist, z.B. im Docker-Setup):
  Daten ueberleben Neustarts, und mehrere Instanzen dieses Services teilen
  sich dieselbe DB - der Service selbst ist damit zustandslos und laesst
  sich horizontal skalieren. Idempotenz ueber den Primary Key event_id
  (INSERT ... ON CONFLICT DO NOTHING) - auch wenn zwei Instanzen dasselbe
  Event gleichzeitig verarbeiten.
- InMemoryHistoryStore (lokale Entwicklung ohne DB, Tests): dieselbe
  Dedupe-Logik ueber ein Set der gesehenen eventIds.

add() gibt zurueck, ob der Eintrag neu war (False = Duplikat, ignoriert).
"""

import logging
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

logger = logging.getLogger("store")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class HistoryEntry:
    event_id: str
    event_type: str
    task_id: str
    owner_user_id: str
    title: str
    status: str
    occurred_at: str
    correlation_id: str = ""


class HistoryStore(Protocol):
    def add(self, entry: HistoryEntry) -> bool: ...
    def list_for_user(self, owner_user_id: str) -> list[HistoryEntry]: ...
    def is_healthy(self) -> bool: ...


class InMemoryHistoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[HistoryEntry] = []
        self._seen: set[str] = set()

    def add(self, entry: HistoryEntry) -> bool:
        with self._lock:
            if entry.event_id in self._seen:
                return False
            self._seen.add(entry.event_id)
            self._entries.append(entry)
            return True

    def list_for_user(self, owner_user_id: str) -> list[HistoryEntry]:
        with self._lock:
            matches = [e for e in self._entries if e.owner_user_id == owner_user_id]
        # Neueste zuerst - nach echtem Zeitpunkt, nicht als Text (Zeitzonen!).
        return sorted(matches, key=lambda e: _parse_time(e.occurred_at), reverse=True)

    def is_healthy(self) -> bool:
        return True


class PostgresHistoryStore:
    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS history (
            event_id       TEXT PRIMARY KEY,
            event_type     TEXT NOT NULL,
            task_id        TEXT NOT NULL,
            owner_user_id  TEXT NOT NULL,
            title          TEXT NOT NULL,
            status         TEXT NOT NULL,
            occurred_at    TIMESTAMPTZ NOT NULL,
            correlation_id TEXT NOT NULL DEFAULT '',
            received_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE INDEX IF NOT EXISTS ix_history_owner ON history (owner_user_id, occurred_at DESC);
    """

    def __init__(self, url: str) -> None:
        from psycopg_pool import ConnectionPool

        # Timeout: lieber schnell 503 als haengende Requests, wenn die DB weg ist.
        self._pool = ConnectionPool(url, min_size=1, max_size=10, timeout=5.0, open=True)
        with self._pool.connection() as conn:
            # Advisory Lock: mehrere gleichzeitig startende Instanzen legen das Schema nicht parallel an.
            conn.execute("SELECT pg_advisory_lock(4711)")
            try:
                conn.execute(self._SCHEMA)
            finally:
                conn.execute("SELECT pg_advisory_unlock(4711)")

    def add(self, entry: HistoryEntry) -> bool:
        with self._pool.connection() as conn:
            cursor = conn.execute(
                """INSERT INTO history (event_id, event_type, task_id, owner_user_id, title, status,
                                        occurred_at, correlation_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (event_id) DO NOTHING""",
                (entry.event_id, entry.event_type, entry.task_id, entry.owner_user_id, entry.title,
                 entry.status, _parse_time(entry.occurred_at), entry.correlation_id),
            )
            return cursor.rowcount == 1

    def list_for_user(self, owner_user_id: str) -> list[HistoryEntry]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT event_id, event_type, task_id, owner_user_id, title, status, occurred_at, correlation_id
                   FROM history WHERE owner_user_id = %s ORDER BY occurred_at DESC""",
                (owner_user_id,),
            ).fetchall()
        return [
            HistoryEntry(
                event_id=r[0], event_type=r[1], task_id=r[2], owner_user_id=r[3], title=r[4], status=r[5],
                occurred_at=r[6].astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                correlation_id=r[7],
            )
            for r in rows
        ]

    def is_healthy(self) -> bool:
        try:
            with self._pool.connection() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False


def create_store() -> HistoryStore:
    url = os.environ.get("DATABASE_URL")
    if url:
        logger.info("History-Speicher: PostgreSQL")
        return PostgresHistoryStore(url)
    logger.warning("DATABASE_URL nicht gesetzt - History nur im Arbeitsspeicher (geht beim Neustart verloren)")
    return InMemoryHistoryStore()
