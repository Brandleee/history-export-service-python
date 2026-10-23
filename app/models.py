"""API-DTOs gemaess contracts/openapi/history-export-service.yaml (1.1.0).

Bewusst getrennt vom internen Speichermodell (store.HistoryEntry): aendert
sich der Vertrag, wird nur hier und in to_dto() angepasst. FastAPI
validiert die Antworten gegen diese Modelle und erzeugt daraus /docs.
"""

from pydantic import BaseModel, ConfigDict, Field

from app.store import HistoryEntry

CONTRACT_VERSION = "1.1.0"


class HistoryEntryDto(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId")
    event_type: str = Field(alias="eventType")
    task_id: str = Field(alias="taskId")
    title: str
    status: str
    occurred_at: str = Field(alias="occurredAt")
    correlation_id: str | None = Field(default=None, alias="correlationId")


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    instance: str
    database: str
    consumer: str


class ErrorResponse(BaseModel):
    error: str
    correlationId: str | None = None


def to_dto(entry: HistoryEntry) -> HistoryEntryDto:
    return HistoryEntryDto(
        event_id=entry.event_id,
        event_type=entry.event_type,
        task_id=entry.task_id,
        title=entry.title,
        status=entry.status,
        occurred_at=entry.occurred_at,
        correlation_id=entry.correlation_id or None,
    )


CSV_FIELDS = ["eventId", "eventType", "taskId", "title", "status", "occurredAt", "correlationId"]
