"""History/Export-Service (Python) des Task-Management-Systems.

Konsumiert asynchron Task-Events (consumer.py) und bietet synchronen
Lesezugriff + Export darauf. Vertrag (verbindlich, Version 1.1.0):
contracts/openapi/history-export-service.yaml

Zustandslos: alle Daten liegen in PostgreSQL (DATABASE_URL), mehrere
Instanzen koennen hinter einem Load-Balancer parallel laufen und teilen sich
die Event-Queue (siehe docker-compose.yml, `--scale history=N`).

Konfiguration per Umgebungsvariable (siehe auch auth.py/consumer.py/store.py):
    PORT            - eigener HTTP-Port (Default 8091, nur im Docker-Image)
    DATABASE_URL    - PostgreSQL; ohne: In-Memory (nur fuer lokale Entwicklung)
    SEQ_URL         - optional: zentrale Log-Aggregation
    START_CONSUMER  - "false" deaktiviert den Event-Consumer (Tests)
"""

import csv
import io
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth import CurrentUser, get_current_user
from app.consumer import DURABLE_QUEUE_NAME, TaskEventConsumer
from app.models import CONTRACT_VERSION, CSV_FIELDS, ErrorResponse, HealthResponse, HistoryEntryDto, to_dto
from app.observability import INSTANCE, correlation_id, resolve_correlation_id, setup_logging
from app.store import HistoryEntry, HistoryStore, InMemoryHistoryStore, create_store
from app.ui import router as ui_router

setup_logging()
logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = create_store()
    app.state.consumer = None
    if os.environ.get("START_CONSUMER", "true").lower() != "false":
        # Nur persistente Instanzen teilen sich die durable Queue (siehe consumer.py).
        in_memory = isinstance(app.state.store, InMemoryHistoryStore)
        app.state.consumer = TaskEventConsumer(app.state.store, None if in_memory else DURABLE_QUEUE_NAME)
        app.state.consumer.start()
    logger.info("History/Export-Service gestartet (Vertrag %s)", CONTRACT_VERSION)
    yield
    if app.state.consumer is not None:
        app.state.consumer.stop()
    logger.info("History/Export-Service beendet")


app = FastAPI(title="History/Export-Service", version=CONTRACT_VERSION, lifespan=lifespan)
app.include_router(ui_router)


@app.middleware("http")
async def correlation_and_access_log(request: Request, call_next):
    token = correlation_id.set(resolve_correlation_id(request.headers.get("X-Correlation-ID")))
    started = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id.get()
        response.headers["X-Instance"] = INSTANCE
        response.headers["Access-Control-Expose-Headers"] = "X-Correlation-ID, X-Instance"
        if request.url.path != "/health":
            logger.info("HTTP-Request bearbeitet", extra={
                "method": request.method, "path": request.url.path, "status": response.status_code,
                "durationMs": round((time.perf_counter() - started) * 1000, 1),
            })
        return response
    finally:
        correlation_id.reset(token)


def _error(status: int, message: str, headers: dict | None = None) -> JSONResponse:
    body = ErrorResponse(error=message, correlationId=correlation_id.get() or None)
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # Vertrag: Fehler immer als {"error": ..., "correlationId": ...}, nicht FastAPIs {"detail": ...}
    return _error(exc.status_code, str(exc.detail), exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _error(400, "Ungueltige Anfrage: " + "; ".join(e["msg"] for e in exc.errors()))


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unbehandelter Fehler")
    return _error(500, "Interner Fehler")


def get_store(request: Request) -> HistoryStore:
    return request.app.state.store


def _load_history(store: HistoryStore, user: CurrentUser) -> list[HistoryEntry]:
    try:
        return store.list_for_user(user.user_id)
    except Exception as exc:
        # Graceful Degradation: DB weg -> sauberes 503 statt Timeout/500.
        logger.error("History konnte nicht gelesen werden: %s", exc)
        raise HTTPException(status_code=503, detail="History voruebergehend nicht verfuegbar") from exc


def _mark_deprecated(response: Response) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Link"] = '</v1/history>; rel="successor-version"'


@app.get("/health", response_model=HealthResponse, responses={503: {"model": ErrorResponse}})
def health(request: Request):
    store_ok = request.app.state.store.is_healthy()
    consumer = request.app.state.consumer
    body = HealthResponse(
        status="UP",
        service="history-export-service-python",
        version=CONTRACT_VERSION,
        instance=INSTANCE,
        database="UP" if store_ok else "DOWN",
        consumer="DISABLED" if consumer is None else ("CONNECTED" if consumer.connected else "RECONNECTING"),
    )
    if not store_ok:
        return _error(503, "Datenbank nicht erreichbar")
    return body


def list_history(
    user: CurrentUser = Depends(get_current_user), store: HistoryStore = Depends(get_store)
) -> list[HistoryEntryDto]:
    return [to_dto(e) for e in _load_history(store, user)]


def export_history(
    format: str = Query(default="json", pattern="^(json|csv)$"),
    user: CurrentUser = Depends(get_current_user),
    store: HistoryStore = Depends(get_store),
):
    entries = [to_dto(e).model_dump(by_alias=True, exclude_none=True) for e in _load_history(store, user)]
    if format == "csv":
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(entries)
        return Response(content=buffer.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="history.csv"'})
    return JSONResponse(content=entries)


_errors = {401: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}

app.add_api_route("/v1/history", list_history, methods=["GET"], response_model=list[HistoryEntryDto],
                  response_model_by_alias=True, response_model_exclude_none=True, responses=_errors,
                  operation_id="listHistoryV1")
app.add_api_route("/v1/export", export_history, methods=["GET"], responses=_errors, operation_id="exportHistoryV1")

# Deprecated: unversionierte Pfade aus Vertrag 1.0.0 (Expand-Phase, siehe contracts/EVOLUTION.md)
app.add_api_route("/history", list_history, methods=["GET"], response_model=list[HistoryEntryDto],
                  response_model_by_alias=True, response_model_exclude_none=True, responses=_errors, deprecated=True,
                  dependencies=[Depends(_mark_deprecated)], operation_id="listHistory")
app.add_api_route("/export", export_history, methods=["GET"], responses=_errors, deprecated=True,
                  dependencies=[Depends(_mark_deprecated)], operation_id="exportHistory")
