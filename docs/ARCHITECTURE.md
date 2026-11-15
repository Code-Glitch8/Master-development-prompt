# Architecture

```
Browser (React SPA)  <--REST /api + WebSocket /ws-->  FastAPI service (127.0.0.1:8765)
                                                       |-- SQLAlchemy -> SQLite (WAL) in data/database
                                                       |-- Engine (asyncio worker, persistent queue)
                                                       |-- Provider adapters (Anthropic / OpenAI / Gemini / Manual)
                                                       |-- Importers, git workspace, checkpoints, export
```

* **Single local process.** The packaged `ProjectPilot.exe` runs uvicorn, the API, the worker and serves the built
  frontend. Single-instance: starting it again just reopens the dashboard.
* **Data** (`%LOCALAPPDATA%\ProjectPilot\data`, overridable with `PROJECT_PILOT_HOME`): `database/`, `projects/`
  (each project is a git workspace), `attachments/`, `conversations/`, `checkpoints/`, `exports/`, `backups/`, `logs/`.
* **Persistent queue.** Task state lives in SQLite. The engine claims tasks with an atomic UPDATE + lock token,
  recovers interrupted work on start, retries retryable errors with exponential backoff and a retry limit, and
  never loops forever. Run states are explicit: `idle / processing / waiting_ai / waiting_user / waiting_provider / completed`.
* **Providers.** `resolve_mode` returns *automated* only when connection type, model and key are all present.
  Adapters are thin HTTP clients using the user's own key (OS keyring or a named environment variable).
* **Changes pipeline.** AI output (`### FILE: path` blocks, or uploaded ZIP/files) -> staged `file_changes` with
  added/modified/deleted/unchanged -> diff review -> approved changes are backed up, applied and committed to the
  project's git workspace. Protected paths are filtered at every step.
* **Checkpoints.** Git-backed snapshot (ZIP fallback) + task-state snapshot; restore is conflict-checked and
  takes a `pre_restore` checkpoint.
* **Security.** Localhost-only bind, Host/Origin checks (DNS-rebinding defence), safe ZIP extraction, path
  validation, git hooks disabled, git URL validation, secrets never logged or returned.
* **Commit-before-response.** `CommitRoute` commits the request's DB session before the response is sent so a
  client's next request always sees the write.
* **Frontend.** React 18 + TypeScript + Vite + Tailwind; a `LiveSocket` with exponential reconnect triggers REST
  refetch so the UI is correct after reconnects.
