# history-export-service-python

History/Export-Service des Task-Management-Systems - konsumiert asynchron
die Task-Events des Task-Service (separates Repo, Delphi) und bietet
Lesezugriff + Export darauf. Geschrieben in Python (FastAPI), **lauffaehiges
Grundgerueest**, kein fertiges Produkt.

Der Vertrag (Endpoints, Felder, Event-Format) liegt im separaten Repo
[`task-management-contracts`](https://github.com/LevinWiederkehr/task-management-contracts) - siehe dort `openapi/history-export-service.yaml`
und `asyncapi/task-events.yaml`. Bei Aenderungen zuerst dort anpassen.

## Was schon funktioniert (Ende-zu-Ende getestet)

- `GET /health` (oeffentlich)
- `GET /history`, `GET /export?format=json|csv`
- Login-Pruefung per Bearer-Token, **inklusive vollstaendiger
  Signaturpruefung** (RS256 gegen die JWKS von Keycloak, siehe `app/auth.py`)
- Ressourcenbasiert: History-Eintraege werden nach `sub`-Claim gefiltert,
  jeder Benutzer sieht nur seine eigene History
- Hintergrund-Consumer (`app/consumer.py`) konsumiert Task-Events vom
  Fanout-Exchange `task-events` (eigene, exklusive Queue - wie beim
  Fanout-Beispiel im [M321-Demo-Projekt](https://github.com/LevinWiederkehr/M321_Basic_Setup)) und fuellt die History live

## Was noch fehlt (TODO fuer die Weiterentwicklung)

- Persistenz: `app/store.py` haelt alles nur im Arbeitsspeicher (weg beim
  Neustart, und der Consumer verpasst alles, was waehrend eines Unterbruchs
  published wurde - bewusstes Fanout-Verhalten, siehe [M321-Demo-Projekt](https://github.com/LevinWiederkehr/M321_Basic_Setup) fuer
  die Diskussion). Fuer eine echte History TODO: Persistenz + idempotente
  Verarbeitung ueber `eventId` (Events koennten doppelt ankommen).
- Pagination/Filterung bei `/history` (z.B. nach Zeitraum, Task-ID).
- Weitere Export-Formate/Optionen bei Bedarf (PDF? Zeitraum-Filter?).
- Tests (aktuell keine automatisierten Tests vorhanden).

## Struktur

| Datei | Zweck |
| --- | --- |
| `app/main.py` | FastAPI-App, REST-Endpoints |
| `app/auth.py` | Access-Token pruefen (Bearer, mit Signaturpruefung) |
| `app/consumer.py` | Hintergrund-Thread, konsumiert Task-Events per AMQP/pika |
| `app/store.py` | Thread-sicherer In-Memory-Speicher |

## Starten

Voraussetzung: gemeinsame Infrastruktur laeuft (siehe
[`task-management-contracts/README.md`](https://github.com/LevinWiederkehr/task-management-contracts#readme)).

Am einfachsten per `start.cmd` (richtet beim ersten Mal die venv ein). Laeuft
der Delphi-Task-Service nicht auf 8090, dessen Port mitgeben - z.B. wenn 8090
belegt ist und der Task-Service mit `start.cmd 8095` gestartet wurde:

```
start.cmd 8095
```

Oder manuell:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8091
```

Konfiguration per Umgebungsvariable (Defaults passen zur gemeinsamen Infra):

| Variable | Default |
| --- | --- |
| `PORT` | `8091` (nur im Docker-Image relevant, lokal per `--port` an uvicorn) |
| `RABBITMQ_HOST` / `RABBITMQ_PORT` | `localhost` / `5672` |
| `OIDC_JWKS_URL` | `http://localhost:8082/realms/task-mgmt/protocol/openid-connect/certs` |
| `OIDC_ISSUER` | `http://localhost:8082/realms/task-mgmt` |
| `TASK_SERVICE_URL` | `http://localhost:8090` (nur fuer die Web-Oberflaeche) |

## Web-Oberflaeche

http://localhost:8091/ui - kleine Testoberflaeche fuer das Gesamtsystem
(nicht Teil des Vertrags, deshalb nicht in `/docs`):

- Login per Benutzername/Passwort (Demo-User aus Keycloak)
- Aufgaben anlegen, erledigen, loeschen - direkt ueber den **Delphi-Task-Service**
  (muss laufen; die Seite sucht ihn automatisch auf 8090 und 8095, andere Ports
  per `TASK_SERVICE_URL` bzw. `start.cmd <Port>`)
- History + CSV-Export aus diesem Service; jede Aenderung an einer Aufgabe
  erscheint hier, nachdem das Event ueber RabbitMQ angekommen ist

## Manuell testen

```
# Token holen (siehe task-management-contracts/README.md fuer Demo-User)
curl -X POST http://localhost:8082/realms/task-mgmt/protocol/openid-connect/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password&client_id=task-mgmt-client&username=levin&password=levin123"

# History abrufen (access_token aus der Antwort oben einsetzen)
curl http://localhost:8091/history -H "Authorization: Bearer <token>"
curl "http://localhost:8091/export?format=csv" -H "Authorization: Bearer <token>"
```

Automatische API-Doku (Swagger UI, von FastAPI generiert): http://localhost:8091/docs
