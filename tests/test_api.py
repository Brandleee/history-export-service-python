"""REST-API gegen den Vertrag contracts/openapi/history-export-service.yaml (1.1.0)."""

import pytest
from fastapi.testclient import TestClient

from app.auth import CurrentUser, get_current_user
from app.main import app


@pytest.fixture
def client(make_entry):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id="user-a", username="levin")
    with TestClient(app) as c:
        app.state.store.add(make_entry("e1", occurred_at="2026-10-01T10:00:00.000Z"))
        app.state.store.add(make_entry("e2", occurred_at="2026-10-01T11:00:00.000Z", correlation_id=""))
        app.state.store.add(make_entry("fremd", owner="user-b"))
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "UP" and body["version"] == "1.1.0"


def test_v1_history_nur_eigene_neueste_zuerst(client):
    body = client.get("/v1/history").json()
    assert [e["eventId"] for e in body] == ["e2", "e1"]
    assert set(body[1]) == {"eventId", "eventType", "taskId", "title", "status", "occurredAt", "correlationId"}
    assert "correlationId" not in body[0]  # optionales Feld fehlt statt null


def test_alter_pfad_ist_deprecated_aber_funktioniert(client):
    response = client.get("/history")
    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"


def test_correlation_id_wird_uebernommen(client):
    response = client.get("/v1/history", headers={"X-Correlation-ID": "abc-123"})
    assert response.headers["X-Correlation-ID"] == "abc-123"


def test_unsichere_correlation_id_wird_ersetzt(client):
    response = client.get("/v1/history", headers={"X-Correlation-ID": "abc 123 <script>"})
    assert response.headers["X-Correlation-ID"] != "abc 123 <script>"


def test_csv_export(client):
    response = client.get("/v1/export?format=csv")
    assert response.headers["content-type"].startswith("text/csv")
    lines = response.text.strip().splitlines()
    assert lines[0] == "eventId,eventType,taskId,title,status,occurredAt,correlationId"
    assert len(lines) == 3


def test_fehlerformat_gemaess_vertrag(client):
    response = client.get("/v1/export?format=pdf")
    assert response.status_code == 400
    assert set(response.json()) == {"error", "correlationId"}


def test_ohne_token_401_im_vertragsformat():
    with TestClient(app) as c:
        response = c.get("/v1/history")
    assert response.status_code == 401
    assert "error" in response.json()


def test_db_ausfall_ergibt_503(client, monkeypatch):
    def broken(user_id):
        raise OSError("db weg")

    monkeypatch.setattr(app.state.store, "list_for_user", broken)
    response = client.get("/v1/history")
    assert response.status_code == 503
    assert response.json()["error"]
