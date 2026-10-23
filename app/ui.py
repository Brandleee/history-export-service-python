"""Kleine Web-Oberflaeche fuer das Gesamtsystem (nicht Teil des Vertrags).

Der Browser spricht direkt mit dem Delphi-Task-Service und mit diesem
Service; nur der Login laeuft ueber /ui/login, damit das Passwort nicht im
Browser-JavaScript gegen Keycloak geschickt werden muss.
"""

import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app.auth import OIDC_ISSUER

OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "task-mgmt-client")
# Im Container ist Keycloak unter einem anderen Host erreichbar als im Issuer steht.
OIDC_TOKEN_URL = os.environ.get("OIDC_TOKEN_URL", f"{OIDC_ISSUER}/protocol/openid-connect/token")
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
            OIDC_TOKEN_URL,
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
