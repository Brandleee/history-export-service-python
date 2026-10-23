import os

import pytest

# Vor dem Import der App: kein echter Consumer, keine Postgres-DB im Test.
os.environ["START_CONSUMER"] = "false"
os.environ.pop("DATABASE_URL", None)
os.environ.pop("SEQ_URL", None)

from app.store import HistoryEntry  # noqa: E402


@pytest.fixture
def make_entry():
    def _make(event_id="e1", owner="user-a", occurred_at="2026-10-01T10:00:00.000Z", **overrides):
        values = dict(event_id=event_id, event_type="TASK_CREATED", task_id="1", owner_user_id=owner,
                      title="Aufgabe", status="OPEN", occurred_at=occurred_at, correlation_id="corr-1")
        values.update(overrides)
        return HistoryEntry(**values)
    return _make
