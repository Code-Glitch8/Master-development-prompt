"""End-to-end demo of Project Pilot with NO external AI account and NO network.

Drives the real API (in-process, throw-away data folder) through: import -> master prompt -> plan ->
approve -> engine hands tasks to the user -> simulated pasted AI answers -> review/approve file changes ->
checkpoints -> handoff export.   Usage: python scripts/demo_workflow.py [--keep]
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

MASTER_PROMPT = "Fix the login error handling and make the page mobile responsive."

FIXED_AUTH = '''USERS = {"ada": "lovelace"}


def login(username, password):
    """Return True only for a known user with the right password; never raise for unknown users."""
    return USERS.get(username) == password
'''
FIXED_HTML = ('<!doctype html>\n<html><head><meta name="viewport" content="width=device-width, initial-scale=1">'
              '<title>Notes</title>\n<style>body{max-width:1200px;margin:0 auto;padding:1rem}</style></head>\n'
              '<body><h1>Notes</h1><p>Sign in to see your notes.</p></body></html>\n')


def say(msg: str = "") -> None:
    print(msg, flush=True)


def sample_zip() -> bytes:
    buf = io.BytesIO()
    src = ROOT / "demo" / "sample-project"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            if f.is_file():
                z.write(f, f"sample-project/{f.relative_to(src).as_posix()}")
        z.writestr("sample-project/.env", "SECRET_KEY=demo-not-a-real-secret\n")  # protected-file demo
    return buf.getvalue()


def simulated_ai_answer(task: dict) -> str:
    """What a user would paste back from an AI website for each task (canned for the demo)."""
    text = f"{task['title']} {task.get('description', '')}".lower()
    if "login" in text or "auth" in text:
        return ("I made unknown users return False instead of raising KeyError.\n\n"
                f"### FILE: app/auth.py\n```\n{FIXED_AUTH}```\n\n"
                "### FILE: .env\n```\nSECRET_KEY=stolen\n```\n")  # protected: must be refused
    if "mobile" in text or "responsive" in text or "style" in text or "css" in text:
        return f"Added a viewport tag and fluid width.\n\n### FILE: static/index.html\n```\n{FIXED_HTML}```\n"
    return f"Analysis complete for: {task['title']}. No file changes needed."


async def run(home: Path) -> int:
    os.environ["PROJECT_PILOT_HOME"] = str(home)
    from fastapi.testclient import TestClient

    from app import config, database
    from app.main import create_app
    from app.workers.engine import Engine

    database.dispose_engine()
    config.reset_settings()
    ok = True
    with TestClient(create_app(no_engine=True)) as c:
        say("1. Importing the sample project from a ZIP ...")
        r = c.post("/api/projects/import/zip", files={"file": ("sample.zip", sample_zip(), "application/zip")})
        r.raise_for_status()
        proj = r.json()
        pid = proj["id"]
        inv = proj["inventory"]
        say(f"   -> {proj['name']}: {inv['file_count']} files, languages {inv['languages']}")

        say("2. Adding a manual (user-assisted) AI account - no API key needed ...")
        acct = c.post("/api/accounts", json={"provider_key": "claude", "display_name": "Demo account"}).json()
        say(f"   -> execution mode: {acct['execution_mode']}")

        say(f"3. Master prompt: {MASTER_PROMPT!r}")
        c.post(f"/api/projects/{pid}/prompts", json={"title": "Demo", "body": MASTER_PROMPT, "is_draft": False})
        plan = c.post(f"/api/projects/{pid}/plan", json={})
        plan.raise_for_status()
        tasks = c.get(f"/api/projects/{pid}/tasks").json()
        say(f"   -> plan has {len(tasks)} tasks (dependencies respected):")
        for t in tasks:
            say(f"      #{t['id']} {t['title']}  [{t['status']}]")

        say("4. Approving the plan and starting execution ...")
        c.put(f"/api/projects/{pid}/providers", json={"account_ids": [acct["id"]]})
        c.post(f"/api/projects/{pid}/plan/approve").raise_for_status()
        c.post(f"/api/projects/{pid}/start").raise_for_status()

        eng = Engine()
        root = Path(proj["directory"])
        for _ in range(40):
            await eng.run_once()
            tasks = c.get(f"/api/projects/{pid}/tasks").json()
            if all(t["status"] in ("completed", "cancelled", "failed") for t in tasks):
                break
            waiting = [t for t in tasks if t["status"] == "awaiting_user"]
            for t in waiting:
                say(f"5. Task #{t['id']} '{t['title']}' is waiting for you (run state: honest 'waiting_user').")
                prompt = c.get(f"/api/tasks/{t['id']}/prompt").json()["prompt"]
                say(f"   prompt to paste into an AI website: {len(prompt)} characters")
                c.post(f"/api/tasks/{t['id']}/submit", data={"response": simulated_ai_answer(t)}).raise_for_status()
            changes = c.get(f"/api/projects/{pid}/changes", params={"decision": "pending"}).json()["changes"]
            for ch in changes:
                if ch["protected"]:
                    r = c.post(f"/api/changes/{ch['id']}/decide", json={"approve": True})
                    say(f"   protected file {ch['path']}: apply attempt -> HTTP {r.status_code} (refused, as designed)")
                    c.post(f"/api/changes/{ch['id']}/decide", json={"approve": False})
                else:
                    say(f"   reviewing {ch['change_type']} {ch['path']} -> approved")
                    c.post(f"/api/changes/{ch['id']}/decide", json={"approve": True})
        tasks = c.get(f"/api/projects/{pid}/tasks").json()
        done = sum(t["status"] == "completed" for t in tasks)
        say(f"6. Result: {done}/{len(tasks)} tasks completed")
        ok &= done == len(tasks)

        auth = (root / "app/auth.py").read_text()
        env = (root / ".env").read_text()
        say(f"   app/auth.py fixed: {'USERS.get' in auth}; .env untouched: {'demo-not-a-real-secret' in env}")
        ok &= "USERS.get" in auth and "demo-not-a-real-secret" in env

        cps = c.get(f"/api/projects/{pid}/checkpoints").json()
        say(f"7. {len(cps)} checkpoints recorded (restorable); handoff files: "
            f"{[p.name for p in root.glob('HANDOFF*')] or 'see export'}")
        exp = c.post(f"/api/projects/{pid}/export/handoff").json()
        say(f"8. Handoff package exported: {exp['file']} ({exp['size']} bytes) in {config.get_settings().exports_dir}")
        ok &= bool(cps)
    database.dispose_engine()
    say("\nDEMO " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the temporary data folder")
    a = ap.parse_args()
    home = Path(tempfile.mkdtemp(prefix="pp-demo-"))
    try:
        return asyncio.run(run(home))
    finally:
        if a.keep:
            say(f"Data kept in {home}")
        else:
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
