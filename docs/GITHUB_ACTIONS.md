# GitHub Actions

Actions **only build, test and distribute**. The shipped application never calls GitHub and runs fully offline
from your Windows machine. Only the free built-in `GITHUB_TOKEN` is used - no paid services, no stored secrets.

| Workflow | Triggers | What it does |
|----------|----------|--------------|
| `test.yml` | every push / PR / manual | **Backend** on Ubuntu + Windows: pinned install, `pip check`, `compileall`, pytest (JUnit report; failing tests listed in the run summary). **Frontend**: `npm ci`, `npm ls`, typecheck, vitest, production build, informational `npm audit`. **Hygiene**: `scripts/check_no_secrets.py` (no credentials/DB files tracked) and workflow YAML validation. |
| `build-windows.yml` | push to any branch (docs-only changes skipped), release published, manual, `workflow_call` | On `windows-latest`: Python 3.12 + Node 20, pinned deps (`backend/constraints.txt`, `package-lock.json`), build React, PyInstaller (`packaging/project_pilot.spec`), **smoke test the built `.exe`** (starts it, checks health, dashboard, API, DB creation), create the ZIP + `SHA256SUMS.txt`, upload as an Actions artifact (30 days). |
| `release.yml` | push of a tag `vX.Y.Z` | Verifies the tag equals `__version__`, calls `build-windows.yml`, then publishes a GitHub Release with the ZIP and checksums (`--generate-notes`; tags containing `-` become prereleases). |

## Releasing

1. Bump `__version__` in `backend/app/__init__.py` (and `frontend/package.json` version), commit and push; wait for green checks.
2. `git tag v0.1.0 && git push origin v0.1.0`.
3. The Release appears with `ProjectPilot-0.1.0-windows.zip` and `SHA256SUMS.txt`.

## Reproducibility

Inputs are pinned: Python packages via `backend/constraints.txt`, npm via `package-lock.json` + `npm ci`, tool
versions via setup actions. The ZIP packer uses sorted entries and fixed timestamps, so identical build output gives
an identical ZIP (checked by a test). The PyInstaller binaries themselves are not guaranteed bit-identical across
runner image updates; `build-info.json` records version and commit.

## Handling failures

Open the failing run -> the *Summary* tab lists failed tests; download `pytest-report-*` artifacts for details.
Reproduce locally with the same commands as the workflow (`pip install ... -c backend/constraints.txt`, `python -m pytest`).
Re-run a build manually from *Actions -> Build Windows -> Run workflow*.

## Security

Workflows run with `contents: read` (only the publish job has `contents: write`). No secrets are stored in the
repo; `check_no_secrets.py` blocks keys, `.env`, `.db` and key files from being committed. Third-party actions are
the official `actions/*` ones.
