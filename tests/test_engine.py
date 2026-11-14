from datetime import timedelta
from pathlib import Path

import pytest

from app.database import session_scope
from app.models import Task, utcnow
from app.providers import ADAPTERS, ExecutionResult, ProviderAdapter, ProviderUnavailable
from app.tasks import queue
from app.workers.engine import Engine

MASTER = "Fix authentication errors and improve mobile responsiveness."


def setup_project(client, project, account_ids, start=True):
    pid = project["id"]
    client.post(f"/api/projects/{pid}/prompts", json={"title": "m", "body": MASTER, "is_draft": False})
    assert client.post(f"/api/projects/{pid}/plan", json={}).status_code == 200
    client.put(f"/api/projects/{pid}/providers", json={"account_ids": account_ids})
    assert client.post(f"/api/projects/{pid}/plan/approve").status_code == 200
    if start:
        assert client.post(f"/api/projects/{pid}/start").status_code == 200
    return pid


def tasks(client, pid):
    return client.get(f"/api/projects/{pid}/tasks").json()


def task(client, tid):
    return client.get(f"/api/tasks/{tid}").json()


class FakeAdapter(ProviderAdapter):
    info = ADAPTERS["claude"].info

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def verify(self, cfg, secret):
        return True, "ok"

    def execute(self, prompt, cfg, secret, timeout=120):
        self.calls.append(prompt)
        step = self.script.pop(0) if self.script else "default response"
        if isinstance(step, Exception):
            raise step
        return ExecutionResult(text=step)


@pytest.fixture()
def auto_account(client, monkeypatch):
    monkeypatch.setenv("PP_FAKE_KEY", "k")
    r = client.post("/api/accounts", json={"provider_key": "claude", "display_name": "Auto", "connection_type": "api_key",
                                           "model": "m", "api_key_env": "PP_FAKE_KEY"})
    assert r.json()["execution_mode"] == "automated"
    return r.json()


def install(monkeypatch, *script):
    fake = FakeAdapter(script)
    monkeypatch.setitem(ADAPTERS, "claude", fake)
    return fake


# ---- user-assisted: honest states ---------------------------------------------------------------

async def test_user_assisted_task_waits_for_user_not_running(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    eng = Engine()
    assert await eng.run_once() is True
    t1 = tasks(client, pid)[0]
    assert t1["status"] == "awaiting_user" and t1["run_state"] == "waiting_user"
    assert eng.status()["running_task_ids"] == []  # nothing claims to be running
    convs = client.get(f"/api/conversations?project_id={pid}").json()
    msgs = client.get(f"/api/conversations/{convs[0]['id']}").json()["messages"]
    kinds = [m["kind"] for m in msgs]
    assert "user_prompt" in kinds and "system_event" in kinds
    assert "user-assisted" in next(m for m in msgs if m["kind"] == "system_event")["content"]
    # second tick: nothing runnable, T1 is waiting on the human and dependents are blocked
    assert await eng.run_once() is False


async def test_dependencies_respected_and_resume_after_user_submits(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    eng = Engine()
    await eng.run_once()
    t1, t2 = tasks(client, pid)[:2]
    assert t2["status"] == "pending"
    r = client.post(f"/api/tasks/{t1['id']}/submit", data={"response": "Structure analysed: React + FastAPI"})
    assert r.status_code == 200 and r.json()["status"] == "completed"
    assert task(client, t2["id"])["status"] == "ready"  # dependency now complete
    assert await eng.run_once() is True
    assert task(client, t2["id"])["status"] == "awaiting_user"
    # the next prompt explicitly carries earlier results (new sessions have no memory)
    p = client.get(f"/api/tasks/{t2['id']}/prompt").json()["prompt"]
    assert "Structure analysed: React + FastAPI" in p and "no memory of previous sessions" in p


async def test_checkpoints_created_before_and_after_tasks(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    eng = Engine()
    await eng.run_once()
    t1 = tasks(client, pid)[0]
    client.post(f"/api/tasks/{t1['id']}/submit", data={"response": "done"})
    reasons = [c["reason"] for c in client.get(f"/api/projects/{pid}/checkpoints").json()]
    assert "before_task" in reasons and "task_completed" in reasons


async def test_handoff_files_written(client, project, manual_account, home):
    pid = setup_project(client, project, [manual_account["id"]])
    await Engine().run_once()
    d = home / "data" / "conversations" / str(pid) / "handoff"
    for name in ("HANDOFF.md", "completed-tasks.json", "unfinished-tasks.json", "project-state.json",
                 "file-manifest.json", "conversation-index.json"):
        assert (d / name).is_file(), name
    md = (d / "HANDOFF.md").read_text()
    assert MASTER in md and "Unfinished tasks" in md and "awaiting_user" in md


async def test_paused_project_is_not_processed(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    assert client.post(f"/api/projects/{pid}/pause").status_code == 200
    assert await Engine().run_once() is False
    assert tasks(client, pid)[0]["status"] == "ready"
    client.post(f"/api/projects/{pid}/resume")
    assert await Engine().run_once() is True


async def test_unapproved_plan_never_executes(client, project, manual_account):
    pid = project["id"]
    client.post(f"/api/projects/{pid}/prompts", json={"body": MASTER, "is_draft": False})
    client.post(f"/api/projects/{pid}/plan", json={})
    client.put(f"/api/projects/{pid}/providers", json={"account_ids": [manual_account["id"]]})
    assert await Engine().run_once() is False
    assert all(t["status"] == "pending" for t in tasks(client, pid))


# ---- queue integrity -----------------------------------------------------------------------------------

async def test_claim_is_exclusive(client, project, manual_account):
    setup_project(client, project, [manual_account["id"]])
    with session_scope() as a:
        first = queue.claim_next(a)
        assert first is not None
        fid = first.id
    with session_scope() as b:
        second = queue.claim_next(b)  # T1 is claimed and its dependents are not ready
        assert second is None
    with session_scope() as c:
        t = c.get(Task, fid)
        assert t.status == "running" and t.lock_token


async def test_recovery_after_restart(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    with session_scope() as db:
        claimed = queue.claim_next(db)
        tid = claimed.id
    # simulate crash: a *new* engine instance starts up and recovers
    with session_scope() as db:
        recovered = queue.recover_interrupted(db)
        assert [t.id for t in recovered] == [tid]
    t = task(client, tid)
    assert t["status"] == "ready" and t["run_state"] == "idle"
    assert await Engine().run_once() is True  # runs again, exactly once
    assert task(client, tid)["status"] == "awaiting_user"


async def test_state_persists_across_engine_restart(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    await Engine().run_once()
    snapshot = [(t["id"], t["status"]) for t in tasks(client, pid)]
    # new Engine() has no in-memory state at all
    eng2 = Engine()
    with session_scope() as db:
        queue.recover_interrupted(db)
    assert [(t["id"], t["status"]) for t in tasks(client, pid)] == snapshot  # awaiting_user is preserved


async def test_engine_start_stop_lifecycle(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    eng = Engine()
    await eng.start()
    import asyncio

    for _ in range(50):
        if task(client, tasks(client, pid)[0]["id"])["status"] == "awaiting_user":
            break
        await asyncio.sleep(0.1)
    assert eng.loop_alive and eng.status()["ws_clients"] == 0  # works with no dashboard connected
    await eng.stop(notify_user=False)
    assert not eng.loop_alive and eng.state == "stopped"
    assert tasks(client, pid)[0]["status"] == "awaiting_user"


# ---- automated execution & failures ---------------------------------------------------------------------------

async def test_automated_flow_stages_files_and_never_overwrites_without_approval(client, project, auto_account, monkeypatch):
    resp = ("Updated login.\n\n### FILE: main.py\n```\nprint('changed')\n```\n\n"
            "### FILE: .env\n```\nSECRET=stolen\n```\n\n### FILE: src/new.ts\n```\nexport const n = 1\n```")
    fake = install(monkeypatch, "analysis done", resp)
    pid = setup_project(client, project, [auto_account["id"]])
    eng = Engine()
    root = Path(project["directory"])
    assert await eng.run_once() is True  # T1 -> completed automatically
    t1, t2 = tasks(client, pid)[:2]
    assert t1["status"] == "completed"
    assert task(client, t1["id"])["actual_output"] == "analysis done"
    assert await eng.run_once() is True  # T2 -> needs_review because files are staged
    t2 = task(client, t2["id"])
    assert t2["status"] == "needs_review" and t2["run_state"] == "completed"
    assert (root / "main.py").read_text() == "print('hi')\n"  # untouched until approved
    ch = client.get(f"/api/projects/{pid}/changes").json()
    assert ch["summary"]["modified"] == 2 and ch["summary"]["added"] == 1
    by_path = {c["path"]: c for c in ch["changes"]}
    assert by_path[".env"]["protected"] is True
    diff = client.get(f"/api/changes/{by_path['main.py']['id']}/diff").json()["diff"]
    assert "-print('hi')" in diff and "+print('changed')" in diff
    # protected file can never be applied
    assert client.post(f"/api/changes/{by_path['.env']['id']}/decide", json={"approve": True}).status_code == 409
    assert (root / ".env").read_text() == "SECRET=abc\n"
    # approve the real ones, reject the secret change
    assert client.post(f"/api/changes/{by_path['main.py']['id']}/decide", json={"approve": True}).status_code == 200
    assert client.post(f"/api/changes/{by_path['src/new.ts']['id']}/decide", json={"approve": True}).status_code == 200
    assert task(client, t2["id"])["status"] == "needs_review"  # .env still pending
    client.post(f"/api/changes/{by_path['.env']['id']}/decide", json={"approve": False})
    assert task(client, t2["id"])["status"] == "completed"
    assert (root / "main.py").read_text() == "print('changed')"
    assert (root / "src" / "new.ts").is_file()
    assert (root / ".env").read_text() == "SECRET=abc\n"
    backups = list((Path(project["directory"]).parents[1] / "attachments" / str(pid) / "backups").rglob("main.py"))
    assert backups and backups[0].read_text() == "print('hi')\n"  # backup made before overwrite
    assert len(fake.calls) == 2


async def test_retryable_error_backs_off_then_stops_no_infinite_loop(client, project, auto_account, monkeypatch):
    err = ProviderUnavailable("HTTP 503", retryable=True)
    fake = install(monkeypatch, err, err, err, err, err)
    pid = setup_project(client, project, [auto_account["id"]])
    t1 = tasks(client, pid)[0]
    client.patch(f"/api/tasks/{t1['id']}", json={"max_retries": 2})
    eng = Engine()
    await eng.run_once()
    t = task(client, t1["id"])
    assert t["status"] == "ready" and t["retry_count"] == 1 and t["run_state"] == "waiting_provider"
    assert t["next_attempt_at"]
    assert await eng.run_once() is False  # backoff time has not passed: not re-run
    assert len(fake.calls) == 1
    for expected in (2,):
        with session_scope() as db:
            db.get(Task, t1["id"]).next_attempt_at = utcnow() - timedelta(seconds=1)
        await eng.run_once()
        assert task(client, t1["id"])["retry_count"] == expected
    with session_scope() as db:
        db.get(Task, t1["id"]).next_attempt_at = utcnow() - timedelta(seconds=1)
    await eng.run_once()  # retries exhausted
    t = task(client, t1["id"])
    assert t["status"] == "awaiting_user" and t["run_state"] == "waiting_provider" and "503" in t["error"]
    assert len(fake.calls) == 3  # initial + 2 retries, never more
    with session_scope() as db:
        assert queue.claim_next(db) is None
    assert any(c["reason"] == "provider_unavailable" for c in client.get(f"/api/projects/{pid}/checkpoints").json())


async def test_auth_error_not_retried_and_user_notified(client, project, auto_account, monkeypatch):
    fake = install(monkeypatch, ProviderUnavailable("authentication rejected (HTTP 401)", retryable=False))
    pid = setup_project(client, project, [auto_account["id"]])
    await Engine().run_once()
    t = tasks(client, pid)[0]
    assert t["status"] == "awaiting_user" and t["run_state"] == "waiting_provider" and t["retry_count"] == 0
    assert len(fake.calls) == 1
    assert any(n["kind"] == "provider_attention" for n in client.get("/api/notifications").json())


async def test_unexpected_exception_retries_then_fails(client, project, auto_account, monkeypatch):
    install(monkeypatch, *[RuntimeError("boom")] * 5)
    pid = setup_project(client, project, [auto_account["id"]])
    t1 = tasks(client, pid)[0]
    client.patch(f"/api/tasks/{t1['id']}", json={"max_retries": 1})
    eng = Engine()
    await eng.run_once()
    with session_scope() as db:
        db.get(Task, t1["id"]).next_attempt_at = utcnow() - timedelta(seconds=1)
    await eng.run_once()
    t = task(client, t1["id"])
    assert t["status"] == "failed" and "boom" in t["error"]
    assert client.post(f"/api/tasks/{t1['id']}/retry").json()["status"] in ("ready", "pending")


async def test_no_silent_account_switching(client, project, auto_account, manual_account, monkeypatch):
    fake = install(monkeypatch, "should not be called")
    pid = setup_project(client, project, [auto_account["id"], manual_account["id"]])
    t1 = tasks(client, pid)[0]
    client.post(f"/api/tasks/{t1['id']}/assign", json={"account_id": auto_account["id"]})
    client.patch(f"/api/accounts/{auto_account['id']}", json={"unavailable_reason": "quota exhausted"})
    await Engine().run_once()
    t = task(client, t1["id"])
    assert t["status"] == "awaiting_user" and t["run_state"] == "waiting_provider"
    assert t["assigned_account_id"] == auto_account["id"]  # NOT switched to the other account
    assert "quota exhausted" in t["error"] and fake.calls == []
    # user explicitly approves reassignment, then it proceeds
    client.patch(f"/api/accounts/{auto_account['id']}", json={"unavailable_reason": ""})
    client.post(f"/api/tasks/{t1['id']}/assign", json={"account_id": manual_account["id"]})
    client.post(f"/api/tasks/{t1['id']}/retry")
    await Engine().run_once()
    assert task(client, t1["id"])["run_state"] == "waiting_user"


async def test_unassigned_task_waits_for_provider(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    t1 = tasks(client, pid)[0]
    client.post(f"/api/tasks/{t1['id']}/assign", json={"account_id": None})
    await Engine().run_once()
    t = task(client, t1["id"])
    assert t["run_state"] == "waiting_provider" and "No provider account" in t["error"]


async def test_cancel_and_retry_controls(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    t1 = tasks(client, pid)[0]
    assert client.post(f"/api/tasks/{t1['id']}/cancel").json()["status"] == "cancelled"
    assert await Engine().run_once() is False
    assert client.post(f"/api/tasks/{t1['id']}/retry").json()["status"] == "ready"
    assert client.post(f"/api/tasks/{t1['id']}/pause").json()["status"] == "paused"
    assert client.post(f"/api/tasks/{t1['id']}/resume").json()["status"] == "ready"
    assert client.post(f"/api/tasks/{t1['id']}/approve").status_code == 409


async def test_project_completes_and_notifies(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    eng = Engine()
    for _ in range(40):
        if not await eng.run_once():
            break
        cur = [t for t in tasks(client, pid) if t["status"] == "awaiting_user"]
        for t in cur:
            client.post(f"/api/tasks/{t['id']}/submit", data={"response": f"result for {t['title']}"})
    assert client.get(f"/api/projects/{pid}").json()["plan_status"] == "completed"
    assert all(t["status"] == "completed" for t in tasks(client, pid))
    kinds = {n["kind"] for n in client.get("/api/notifications").json()}
    assert {"project_completed", "task_completed", "user_action_required"} <= kinds
