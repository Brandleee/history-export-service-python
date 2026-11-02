"""Kleine Web-Oberflaeche fuer das Gesamtsystem (http://localhost:8091/ui).

Aufgaben werden direkt im Browser ueber den Delphi-Task-Service verwaltet
(der erlaubt CORS); dessen Events landen per RabbitMQ in der History, die die
Seite von diesem Service liest.

Nicht Teil des Vertrags (contracts/openapi/history-export-service.yaml) - die
Routen sind deshalb aus der OpenAPI-Doku ausgeblendet. /ui/login leitet den
Login an Keycloak weiter, weil Keycloak keine Anfragen direkt aus dem Browser
erlaubt (kein CORS fuer den Client).
"""

import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.auth import OIDC_ISSUER

OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "task-mgmt-client")
TASK_SERVICE_URL = os.environ.get("TASK_SERVICE_URL", "http://localhost:8090")
# Die Seite sucht den Delphi-Service der Reihe nach unter diesen Adressen. 8095 ist
# der Ausweich-Port aus dem Delphi-Repo (run.cmd/start.cmd), falls 8090 belegt ist.
_TASK_SERVICE_CANDIDATES = list(dict.fromkeys([TASK_SERVICE_URL, "http://localhost:8090", "http://localhost:8095"]))
_INDEX_HTML = Path(__file__).parent / "static" / "index.html"

router = APIRouter(prefix="/ui", include_in_schema=False)


@router.get("")
def index() -> FileResponse:
    return FileResponse(_INDEX_HTML)


@router.get("/config")
def config() -> dict:
    return {"taskServiceUrls": _TASK_SERVICE_CANDIDATES}


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
