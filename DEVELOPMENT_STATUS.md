# Development status

Last verified: backend **109 pytest tests pass**, frontend **25 vitest tests pass**, `tsc --noEmit` clean, frozen
Linux build of the PyInstaller bundle passes `scripts/smoke_test.py`, demo workflow passes.

| Stage | Status |
|-------|--------|
| Backend: models (17 tables), config, security utils, safe ZIP | Done, tested |
| Provider/account manager (honest modes, keyring/env keys) | Done, tested (HTTP adapters tested with fakes - no live provider calls made) |
| Project importer (ZIP/folder/Git URL/files) + inventory detection | Done, tested |
| Master prompt, deterministic task planner, dependency graph | Done, tested |
| Persistent queue + asyncio engine (claim, recovery, backoff, pause/resume) | Done, tested |
| Conversations, attachments, staged changes + diff review, protected files | Done, tested |
| Checkpoints, safe restore, git history, export, HANDOFF.md/JSON | Done, tested |
| Notifications, WebSocket live monitoring, activity log, settings | Done, tested |
| Frontend (dashboard, projects, tasks, accounts, activity, settings, mobile + light/dark) | Done; verified in a real browser end to end |
| Demo project and walkthrough | Done (`scripts/demo_workflow.py`, tested in CI) |
| Windows packaging (PyInstaller spec, install/run/stop/uninstall scripts, deterministic ZIP) | Done; build verified on Linux, **not yet executed on Windows** |
| GitHub Actions (`test.yml`, `build-windows.yml`, `release.yml`) | Written and YAML-validated; see "CI results" below |

## CI results
Latest push to `main`: **Test** (backend on Ubuntu + Windows, frontend, secret scan) and **Build Windows**
(PyInstaller build on `windows-latest` + smoke test of the built `.exe` + ZIP artifact) all passed.
The first Windows test run caught a real bug (project deletion left git's read-only files behind); it is fixed and covered by a test.
`release.yml` has **not been exercised yet**: the tag push from the development session was blocked, so no release exists until you push a tag (see README).

## Known limitations (honest list)
* **Windows-only pieces are unverified locally**: the development sandbox is Linux. The `.bat` scripts, the
  Windows keyring backend, PowerShell toast notifications and the `.exe` itself are exercised only by GitHub's
  Windows runners (build + smoke test), not by a person on a Windows desktop. Please report issues.
* The executable is **unsigned**; SmartScreen will warn.
* Automated provider calls use the public HTTP APIs with a model name you supply; they were tested against fakes,
  not live accounts. Consumer web chats (ChatGPT, Claude.ai, Gemini web...) are user-assisted by design.
* The task planner is deterministic keyword/heuristic based (no AI call), so plans for unusual prompts should be
  edited before approval.
* Releases are reproducible in inputs (pinned deps, lockfile, deterministic ZIP) but PyInstaller output is not
  bit-for-bit identical across runner image updates.
* Single-user, single-machine by design: no auth on the localhost API (protected by loopback bind + Host/Origin checks).
* No installer `.exe`/MSI: distribution is a ZIP with `install.bat`.
