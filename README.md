# history-export-service-python

History/Export-Service des Task-Management-Systems - konsumiert asynchron
die Task-Events des Task-Service (separates Repo, Delphi) und bietet
Lesezugriff + Export darauf. Geschrieben in Python (FastAPI).

Der Vertrag (Endpoints, Felder, Event-Format) liegt im separaten Repo
[`task-management-contracts`](https://github.com/LevinWiederkehr/task-management-contracts) -
siehe dort `openapi/history-export-service.yaml` und `asyncapi/task-events.yaml`
(implementierte Version: **1.1.0**). Bei Aenderungen zuerst dort anpassen,
Vorgehen siehe `EVOLUTION.md` im contracts-Repo.

## Funktionen

- `GET /health` (oeffentlich: DB-Status, Consumer-Status, antwortende Instanz)
- `GET /v1/history`, `GET /v1/export?format=json|csv`
- `/history`, `/export` ohne Version: **deprecated** Alias aus 1.0.0 (Header `Deprecation: true`)
- **Authentifizierung**: Bearer-Token mit Signaturpruefung (RS256 gegen die
  Keycloak-JWKS, Key-Rotation wird erkannt), Issuer und Ablaufzeit
- **Autorisierung**: ressourcenbasiert ueber den `sub`-Claim
- **Fehlerformat** gemaess Vertrag: `{"error": ..., "correlationId": ...}`
- **Asynchron + entkoppelt**: durable Queue `history-export-service.task-events`
  am Fanout-Exchange - Events bleiben liegen, waehrend der Service weg ist
- **Idempotent**: at-least-once-Zustellung, Duplikate werden ueber `eventId`
  verworfen (Primary Key in PostgreSQL)
- **Tolerant Reader**: unbekannte Felder werden ignoriert, fehlende optionale
  Felder bekommen Defaults, unbekannte `schemaVersion`/`eventType` werden
  uebersprungen statt die Queue zu blockieren (`app/events.py`)
- **Zustandslos + skalierbar**: Daten in PostgreSQL, beliebig viele Instanzen
  hinter nginx (Round-Robin, dynamisch per Docker-DNS), Competing Consumers
  auf derselben Queue
- **Resilienz**: Reconnect mit exponentiellem Backoff (RabbitMQ), Requeue bei
  DB-Ausfall, 503 statt Timeout wenn DB/Keycloak weg sind, Timeouts + Retry
  auf andere Instanz im Load-Balancer, Graceful Shutdown des Consumers
- **Observability**: Korrelations-ID aus Request-Header bzw. Event, JSON-Logs
  (CLEF) inkl. Instanz-Name, zentral in Seq

## Struktur

| Datei | Zweck |
| --- | --- |
| `app/main.py` | FastAPI-App, REST-Endpoints, Middleware (Correlation-ID, Request-Log), Fehlerformat |
| `app/models.py` | API-DTOs gemaess Vertrag (getrennt vom Speichermodell) |
| `app/events.py` | Event lesen nach Tolerant-Reader-Prinzip |
| `app/consumer.py` | AMQP-Consumer (pika): durable Queue, Ack/Nack, Idempotenz, Reconnect |
| `app/store.py` | PostgreSQL-Store (idempotent) bzw. In-Memory fuer lokale Entwicklung/Tests |
| `app/auth.py` | Token-Pruefung (Bearer, Signatur, Issuer) |
| `app/observability.py` | JSON-Logging, Korrelations-ID, Versand an Seq |
| `app/ui.py`, `app/static/` | Kleine Web-Oberflaeche fuer das Gesamtsystem |
| `tests/` | pytest: Vertrag, Tolerant Reader, Idempotenz, Consumer-Verhalten |
| `docker-compose.yml`, `nginx.conf` | Service + eigene DB + Load-Balancer |

## Starten

**Gesamtsystem**: `start-all.ps1` im contracts-Repo (startet auch diesen
Service in Docker). Nur dieser Service (gemeinsame Infra muss laufen):

```
docker compose up -d --build                  # 2 Instanzen + Postgres + nginx auf :8091
docker compose up -d --scale history=4        # hochskalieren, ohne Unterbruch
```

Lokale Entwicklung ohne Docker (In-Memory, eigene temporaere Queue - nimmt
den Docker-Instanzen also keine Events weg):

```
start.cmd [Port des Delphi-Task-Service]
```

| Adresse | Was |
| --- | --- |
| http://localhost:8091/ui | Web-Oberflaeche (Login `levin`/`levin123`) |
| http://localhost:8091/docs | Swagger UI (von FastAPI generiert) |

Konfiguration per Umgebungsvariable:

| Variable | Default |
| --- | --- |
| `PORT` | `8091` (nur im Docker-Image, lokal per `--port`) |
| `DATABASE_URL` | leer = In-Memory; Docker: `postgresql://history:history@history-db:5432/history` |
| `RABBITMQ_HOST` / `RABBITMQ_PORT` | `localhost` / `5672` |
| `RABBITMQ_USER` / `RABBITMQ_PASS` | `guest` / `guest` |
| `OIDC_ISSUER` | `http://localhost:8082/realms/task-mgmt` |
| `OIDC_JWKS_URL` | `<Issuer>/protocol/openid-connect/certs` auf localhost:8082 |
| `OIDC_TOKEN_URL` | `<Issuer>/protocol/openid-connect/token` (nur fuer den UI-Login) |
| `TASK_SERVICE_URL` | `http://localhost:8090` (nur fuer die Web-Oberflaeche) |
| `SEQ_URL` | leer (kein Versand); Docker: `http://seq:80` |
| `START_CONSUMER` | `true` (`false` in Tests) |

## Tests und Lint

```
pip install -r requirements-dev.txt
ruff check .
pytest
```

Laeuft bei jedem Push in GitHub Actions (`.github/workflows/ci.yml`, inkl.
Docker-Build). End-to-End-Test des Gesamtsystems: `scripts/e2e-test.ps1`
im contracts-Repo.
