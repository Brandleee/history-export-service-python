"""Strukturiertes Logging + Korrelations-ID.

Jede Log-Zeile ist ein JSON-Objekt im CLEF-Format (wie beim Delphi-Task-
Service, siehe dessen AppLog.pas) mit Zeitstempel (UTC), Level, Service,
Instanz (Hostname - unterscheidet skalierte Container) und der
Korrelations-ID des aktuellen Requests bzw. Events.

Die Korrelations-ID liegt in einer ContextVar: die HTTP-Middleware (main.py)
setzt sie pro Request, der Consumer (consumer.py) pro verarbeitetem Event.
Alle Log-Aufrufe darin bekommen sie automatisch mit.

Ist SEQ_URL gesetzt, werden die Logs zusaetzlich gebuendelt an Seq (zentrale
Log-Aggregation) geschickt - in einem Hintergrund-Thread, ein langsames oder
ausgefallenes Seq bremst also nie einen Request.
"""

import json
import logging
import os
import queue
import re
import socket
import threading
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

import httpx

SERVICE_NAME = "history-export-service-python"
INSTANCE = socket.gethostname()

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")

_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# Attribute, die jeder LogRecord sowieso hat - alles andere kam per extra={...}.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


def resolve_correlation_id(value: str | None) -> str:
    """Uebernimmt eine harmlose Korrelations-ID vom Aufrufer, sonst neue UUID."""
    if value and _SAFE_ID.match(value):
        return value
    return str(uuid.uuid4())


class ClefFormatter(logging.Formatter):
    _LEVELS = {"DEBUG": "Debug", "INFO": "Information", "WARNING": "Warning", "ERROR": "Error", "CRITICAL": "Fatal"}

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "@t": datetime.fromtimestamp(record.created, tz=UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "@l": self._LEVELS.get(record.levelname, record.levelname),
            "@m": record.getMessage(),
            "service": SERVICE_NAME,
            "instance": INSTANCE,
            "logger": record.name,
        }
        cid = correlation_id.get()
        if cid:
            entry["correlationId"] = cid
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["@x"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class SeqHandler(logging.Handler):
    """Sendet Log-Zeilen gebuendelt an Seq (/api/events/raw?clef)."""

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url.rstrip("/") + "/api/events/raw?clef"
        self._queue: queue.Queue[str] = queue.Queue(maxsize=10000)
        threading.Thread(target=self._ship, name="seq-shipper", daemon=True).start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._queue.put_nowait(self.format(record))
        except queue.Full:
            pass  # lieber Logs verwerfen als die Anwendung blockieren

    def _ship(self) -> None:
        client = httpx.Client(timeout=2.0)
        while True:
            batch = [self._queue.get()]
            while not self._queue.empty() and len(batch) < 500:
                batch.append(self._queue.get_nowait())
            try:
                client.post(self._url, content="\n".join(batch).encode(),
                            headers={"Content-Type": "application/vnd.serilog.clef"})
            except httpx.HTTPError:
                pass  # Seq nicht erreichbar - stdout hat die Logs ohnehin


def setup_logging() -> None:
    formatter = ClefFormatter()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    seq_url = os.environ.get("SEQ_URL")
    if seq_url:
        handlers.append(SeqHandler(seq_url))
    for handler in handlers:
        handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = handlers
    root.setLevel(os.environ.get("LOG_LEVEL", "INFO"))
    # Eigene Request-Logs (Middleware) ersetzen die unstrukturierten Access-Logs von uvicorn.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("pika").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
