"""Kleine Web-Oberflaeche fuer das Gesamtsystem (http://localhost:8091/ui).

Aufgaben werden direkt im Browser ueber den Delphi-Task-Service verwaltet
(der erlaubt CORS); dessen Events landen per RabbitMQ in der History, die die
Seite von diesem Service liest.

Nicht Teil des Vertrags (contracts/openapi/history-export-service.yaml) - die
Routen sind deshalb aus der OpenAPI-Doku ausgeblendet. /ui/login leitet den
Login an Keycloak weiter, weil Keycloak keine Anfragen direkt aus dem Browser
erlaubt (kein CORS fuer den Client). Laeuft der Delphi-Service nicht, nutzt die
Seite /ui/sim/tasks als Ersatz (siehe Simulationsmodus unten).
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
import pika
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from app.auth import OIDC_ISSUER, CurrentUser, get_current_user
from app.consumer import EXCHANGE_NAME, RABBITMQ_HOST, RABBITMQ_PORT

OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "task-mgmt-client")
TASK_SERVICE_URL = os.environ.get("TASK_SERVICE_URL", "http://localhost:8090")
_INDEX_HTML = Path(__file__).parent / "static" / "index.html"

router = APIRouter(prefix="/ui", include_in_schema=False)


@router.get("")
def index() -> FileResponse:
    return FileResponse(_INDEX_HTML)


@router.get("/config")
def config() -> dict:
    return {"taskServiceUrl": TASK_SERVICE_URL}


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(credentials: LoginRequest) -> JSONResponse:
    try:
        response = httpx.post(
            f"{OIDC_ISSUER}/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": OIDC_CLIENT_ID,
                "username": credentials.username,
                "password": credentials.password,
            },
            timeout=5.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Keycloak nicht erreichbar: {exc}") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Benutzername oder Passwort falsch")
    return JSONResponse(content={"access_token": response.json()["access_token"]})


# --- Simulationsmodus -------------------------------------------------------
# Ersatz fuer den Delphi-Task-Service, falls der nicht laeuft (z.B. kein Delphi
# installiert). Gleiche Endpoints wie im Vertrag (contracts/openapi/task-service.yaml)
# und gleiche Events (contracts/asyncapi/task-events.yaml), nur im Arbeitsspeicher.

class TaskCreateRequest(BaseModel):
    title: str
    description: str | None = None


class TaskUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    status: Literal["OPEN", "DONE"] | None = None


_sim_tasks: dict[str, dict] = {}
_sim_lock = threading.Lock()


def _publish_task_event(event_type: str, task: dict, task_status: str) -> None:
    payload = {
        "eventId": str(uuid.uuid4()),
        "eventType": event_type,
        "taskId": task["id"],
        "ownerUserId": task["ownerUserId"],
        "title": task["title"],
        "status": task_status,
        "occurredAt": datetime.now(timezone.utc).isoformat(),
    }
    connection = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT))
    try:
        connection.channel().basic_publish(exchange=EXCHANGE_NAME, routing_key="", body=json.dumps(payload))
    finally:
        connection.close()


def _own_task(task_id: str, user: CurrentUser) -> dict:
    task = _sim_tasks.get(task_id)
    if task is None or task["ownerUserId"] != user.user_id:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    return task


@router.get("/sim/tasks")
def sim_list_tasks(user: CurrentUser = Depends(get_current_user)) -> list[dict]:
    with _sim_lock:
        return [t for t in _sim_tasks.values() if t["ownerUserId"] == user.user_id]


@router.post("/sim/tasks", status_code=201)
def sim_create_task(body: TaskCreateRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    task = {
        "id": str(uuid.uuid4()),
        "ownerUserId": user.user_id,
        "title": body.title,
        "description": body.description or "",
        "status": "OPEN",
        "createdAt": now,
        "updatedAt": now,
    }
    with _sim_lock:
        _sim_tasks[task["id"]] = task
    _publish_task_event("TASK_CREATED", task, "OPEN")
    return task


@router.put("/sim/tasks/{task_id}")
def sim_update_task(task_id: str, body: TaskUpdateRequest, user: CurrentUser = Depends(get_current_user)) -> dict:
    with _sim_lock:
        task = _own_task(task_id, user)
        task.update(body.model_dump(exclude_none=True))
        task["updatedAt"] = datetime.now(timezone.utc).isoformat()
    event_type = "TASK_COMPLETED" if body.status == "DONE" else "TASK_UPDATED"
    _publish_task_event(event_type, task, task["status"])
    return task


@router.delete("/sim/tasks/{task_id}", status_code=204)
def sim_delete_task(task_id: str, user: CurrentUser = Depends(get_current_user)) -> Response:
    with _sim_lock:
        task = _sim_tasks.pop(_own_task(task_id, user)["id"])
    _publish_task_event("TASK_DELETED", task, "DELETED")
    return Response(status_code=204)
