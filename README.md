# Project Pilot

A **local Windows project manager for multi-AI development**. Import a project, write a master prompt, let Project Pilot
break it into dependency-ordered tasks, and hand each task to your AI accounts (automatically through official APIs
where you have configured them, or by copy/paste where you haven't). It tracks conversations, reviews every file
change before it touches your project, keeps restorable checkpoints, and writes a `HANDOFF.md` so any AI (or person)
can pick up where the last one stopped.

> **Everything runs on your computer.** The database, project files, conversations, checkpoints and background tasks
> never leave it. GitHub is used only to build and download releases - Project Pilot never needs GitHub (or Actions)
> to run. The service binds to `127.0.0.1` only.

## Install (Windows 10/11)

1. Download `ProjectPilot-<version>-windows.zip` from the [Releases](../../releases) page (and compare its SHA-256 with `SHA256SUMS.txt`).
2. Extract it, double-click **`install.bat`** (per-user, no admin rights), then open **Project Pilot** from the Start Menu.
3. The dashboard opens at <http://127.0.0.1:8765>.

`run.bat` runs it from the extracted folder without installing; `stop.bat` stops the background service;
`uninstall.bat` removes the app and asks before deleting your data (kept by default).

Data lives in `%LOCALAPPDATA%\ProjectPilot\data`. API keys go to Windows Credential Manager (or an environment
variable you name) - never to files or the database. The app is unsigned, so SmartScreen may warn: *More info -> Run anyway*.

## How it works honestly

Each AI account has one of three modes, shown everywhere in the UI:

| Mode | When | What Project Pilot does |
|------|------|-------------------------|
| **Automated** | connection type *API key*, a model name and a key are all configured | calls the provider's official API, stages returned files for your review |
| **User-assisted** | the account is used via the provider's website | prepares the exact prompt, then **waits for you** (state "Waiting for you"), you paste the answer back |
| **Manual-only** | custom/other providers | same as user-assisted, no integration |

It never logs in to consumer websites, never scrapes, never bypasses limits, and never silently switches accounts.
If a provider is unavailable the task waits (with backoff) and you are notified.

Safety: file changes are *staged*, shown as diffs, and applied only when you approve; protected files (`.env`, keys,
databases) can never be overwritten, shown or exported; a checkpoint is taken before/after every task, and restore
refuses to discard newer work unless you force it (and then saves a safety checkpoint first). ZIP imports are
validated against path traversal, symlinks and zip bombs.

## Try the demo (no AI account, no network)

    python scripts/demo_workflow.py

Runs import -> plan -> approve -> paste simulated answers -> review changes -> checkpoints -> handoff export against
the real API. See [demo/README.md](demo/README.md).

## Develop

Requirements: Python 3.12+, Node 20+, Git.

    # backend
    python -m venv .venv && .venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
    pip install -r backend/requirements-dev.txt -c backend/constraints.txt
    python -m pytest                                          # backend tests
    python backend/run_service.py --no-browser                # API + dashboard on :8765

    # frontend
    cd frontend && npm ci
    npm run dev          # Vite dev server, proxies /api and /ws to :8765
    npm test             # vitest
    npm run build        # production bundle -> frontend/dist (served by the backend)

Layout: `backend/app` (FastAPI, SQLAlchemy models, worker engine, providers, importers, checkpoints, export),
`frontend/src` (React + TypeScript + Tailwind), `tests/`, `scripts/` (build, smoke test, demo, secret scan),
`packaging/` (PyInstaller spec, Windows `.bat` scripts), `.github/workflows/`, `docs/`, `demo/`.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Build and release

Locally on Windows: `scripts\build_windows.bat`. On GitHub: [docs/GITHUB_ACTIONS.md](docs/GITHUB_ACTIONS.md).
To release: bump `__version__` in `backend/app/__init__.py`, commit, then `git tag vX.Y.Z && git push --tags`.

## Status and limitations

See [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md) for per-stage status, test results and known limitations.
