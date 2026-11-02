@echo off
rem Startet den History/Export-Service. Optional: start.cmd <Port des Delphi-Task-Service>
rem Default ist 8090 (siehe contracts/openapi/task-service.yaml). Laeuft der
rem Task-Service auf einem anderen Port (z.B. start.cmd 8095 im Delphi-Repo, weil
rem 8090 belegt ist), denselben Port hier angeben, z.B.: start.cmd 8095
setlocal
set "TASK_PORT=%~1"
if "%TASK_PORT%"=="" set "TASK_PORT=8090"
set "TASK_SERVICE_URL=http://localhost:%TASK_PORT%"

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Richte virtuelle Umgebung ein ...
  python -m venv "%~dp0.venv" || exit /b 1
  "%~dp0.venv\Scripts\python" -m pip install -r "%~dp0requirements.txt" || exit /b 1
)

echo Starte History/Export-Service auf http://localhost:8091 ... (Beenden mit Ctrl+C)
echo Web-Oberflaeche: http://localhost:8091/ui   Task-Service: %TASK_SERVICE_URL%
cd /d "%~dp0"
".venv\Scripts\python" -m uvicorn app.main:app --reload --port 8091
