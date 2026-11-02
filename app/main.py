"""History/Export-Service (Python) - Grundgerueest fuer das Task-Management-System.

Konsumiert asynchron Task-Events (siehe consumer.py) und bietet synchronen
Lesezugriff + Export darauf. Vertrag (verbindlich):
../../contracts/openapi/history-export-service.yaml

Konfiguration per Umgebungsvariable (siehe auth.py/consumer.py fuer weitere):
    PORT - eigener HTTP-Port (Default 8091)
"""

import csv
import io
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query
from fastapi.responses import JSONResponse, Response

from app.auth import CurrentUser, get_current_user
from app.consumer import start_consumer_thread
from app.store import HistoryEntry, store


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_consumer_thread()
    yield


app = FastAPI(title="History/Export-Service", lifespan=lifespan)


def _entry_to_dict(entry: HistoryEntry) -> dict:
    return {
        "eventId": entry.event_id,
        "eventType": entry.event_type,
        "taskId": entry.task_id,
        "title": entry.title,
        "status": entry.status,
        "occurredAt": entry.occurred_at,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "UP", "service": "history-export-service-python"}


@app.get("/history")
def list_history(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    return [_entry_to_dict(e) for e in store.list_for_user(user.user_id)]


@app.get("/export")
def export_history(
    format: str = Query(default="json", pattern="^(json|csv)$"),
    user: CurrentUser = Depends(get_current_user),
):
    entries = store.list_for_user(user.user_id)

    if format == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer, fieldnames=["eventId", "eventType", "taskId", "title", "status", "occurredAt"]
        )
        writer.writeheader()
        for entry in entries:
            writer.writerow(_entry_to_dict(entry))
        return Response(content=buffer.getvalue(), media_type="text/csv")

    return JSONResponse(content=[_entry_to_dict(e) for e in entries])
