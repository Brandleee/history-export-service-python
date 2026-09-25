"""Prueft OAuth2-Access-Tokens (JWT, RS256) gegen die JWKS von Keycloak.

Im Gegensatz zum Delphi-Task-Service (siehe dessen JwtAuth.pas, dort noch
ohne Signaturpruefung als offenes TODO) wird hier die Signatur VOLLSTAENDIG
verifiziert - Python hat dafuer mit python-jose eine gut getestete
Bibliothek, das lohnt sich hier direkt richtig zu machen.

Vertrag: siehe ../../contracts/openapi/history-export-service.yaml
(securitySchemes.oauth2Password).
"""

import os
import time

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt
from jose.exceptions import JWTError

OIDC_JWKS_URL = os.environ.get(
    "OIDC_JWKS_URL", "http://localhost:8082/realms/task-mgmt/protocol/openid-connect/certs"
)
OIDC_ISSUER = os.environ.get("OIDC_ISSUER", "http://localhost:8082/realms/task-mgmt")

_bearer_scheme = HTTPBearer(auto_error=False)

_jwks_cache: dict | None = None
_jwks_cached_at: float = 0.0
_JWKS_CACHE_SECONDS = 3600


def _get_jwks() -> dict:
    global _jwks_cache, _jwks_cached_at
    now = time.monotonic()
    if _jwks_cache is None or (now - _jwks_cached_at) > _JWKS_CACHE_SECONDS:
        response = httpx.get(OIDC_JWKS_URL, timeout=5.0)
        response.raise_for_status()
        _jwks_cache = response.json()
        _jwks_cached_at = now
    return _jwks_cache


class CurrentUser:
    def __init__(self, user_id: str, username: str):
        self.user_id = user_id  # "sub"-Claim - massgeblich fuer History-Zugehoerigkeit
        self.username = username


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Fehlender oder ungueltiger Authorization-Header (Bearer-Token erforderlich)",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    try:
        unverified_header = jwt.get_unverified_header(token)
        jwks = _get_jwks()
        key = next(
            (k for k in jwks.get("keys", []) if k.get("kid") == unverified_header.get("kid")),
            None,
        )
        if key is None:
            raise JWTError("Kein passender Schluessel (kid) in der JWKS gefunden")

        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=OIDC_ISSUER,
            options={"verify_aud": False},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token ungueltig oder abgelaufen: {exc}",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"JWKS von Keycloak nicht erreichbar: {exc}",
        ) from exc

    return CurrentUser(user_id=claims["sub"], username=claims.get("preferred_username", ""))
