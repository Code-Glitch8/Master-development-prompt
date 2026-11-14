import json
import zipfile
from pathlib import Path

from conftest import make_zip
from test_engine import MASTER, setup_project, tasks


# ---- attachments / file handoff -----------------------------------------------------------------

def upload(client, pid, name, data, **form):
    return client.post(f"/api/projects/{pid}/attachments", files={"file": (name, data)}, data=form)


def test_zip_attachment_preserves_hierarchy_and_stages_diff(client, project):
    pid = project["id"]
    z = make_zip({"main.py": "print('v2')\n", "README.md": "# Demo\n", "pkg/sub/new.py": "x=1\n", "../evil.py": "x"})
    assert upload(client, pid, "update.zip", z).status_code == 400  # traversal rejects the whole archive
    z = make_zip({"main.py": "print('v2')\n", "README.md": "# Demo\n", "pkg/sub/new.py": "x=1\n"})
    r = upload(client, pid, "update.zip", z)
    assert r.status_code == 201
    by = {c["path"]: c["change_type"] for c in r.json()["changes"]}
    assert by == {"main.py": "modified", "README.md": "unchanged", "pkg/sub/new.py": "added"}
    root = Path(project["directory"])
    assert not (root / "pkg").exists()  # nothing applied yet
    pending = [c for c in r.json()["changes"] if c["decision"] == "pending"]
    assert len(pending) == 2  # unchanged is auto-resolved
    res = client.post(f"/api/projects/{pid}/changes/decide-all", json={"approve": True}).json()
    assert res["decided"] == 2 and not res["errors"]
    assert (root / "pkg" / "sub" / "new.py").read_text() == "x=1\n"
    assert (root / "main.py").read_text() == "print('v2')\n"
    files = {f["path"] for f in client.get(f"/api/projects/{pid}/files").json()}
    assert "pkg/sub/new.py" in files  # inventory updated
    hist = client.get(f"/api/projects/{pid}/history").json()
    assert any("apply" in h["subject"] for h in hist)  # recorded in git history


def test_zip_wrapper_folder_only_stripped_when_named_after_project(client, project):
    pid = project["id"]
    # a lone `src/` folder must NOT be flattened into the project root
    r = upload(client, pid, "a.zip", make_zip({"src/only.ts": "1"}))
    assert [c["path"] for c in r.json()["changes"]] == ["src/only.ts"]
    # a wrapper named after the project IS removed (AI re-zipped the whole project)
    r = upload(client, pid, "b.zip", make_zip({"main.py": "print('hi')\n"}, wrap="demo"))
    assert [(c["path"], c["change_type"]) for c in r.json()["changes"]] == [("main.py", "unchanged")]
    # any other wrapper is preserved verbatim
    r = upload(client, pid, "c.zip", make_zip({"main.py": "x"}, wrap="whatever"))
    assert [c["path"] for c in r.json()["changes"]] == ["whatever/main.py"]


def test_full_snapshot_reports_deleted_but_keeps_protected(client, project):
    pid = project["id"]
    z = make_zip({"main.py": "print('hi')\n"})
    r = upload(client, pid, "snap.zip", z, full_snapshot="true")
    ch = {c["path"]: c for c in r.json()["changes"]}
    assert ch["main.py"]["change_type"] == "unchanged"
    assert ch["package.json"]["change_type"] == "deleted"
    assert ch[".env"]["protected"] is True
    root = Path(project["directory"])
    client.post(f"/api/projects/{pid}/changes/decide-all", json={"approve": True})
    assert not (root / "package.json").exists()  # approved deletion (backed up)
    assert (root / ".env").exists()  # protected: never deleted
    summ = client.get(f"/api/projects/{pid}/changes").json()["summary"]
    assert summ["deleted"] >= 1 and summ["unchanged"] == 1


def test_reject_leaves_file_untouched(client, project):
    pid = project["id"]
    r = upload(client, pid, "main.py", b"print('nope')\n")
    c = r.json()["changes"][0]
    assert c["change_type"] == "modified" or c["path"] == "attachments/main.py"
    cid = c["id"]
    client.post(f"/api/changes/{cid}/decide", json={"approve": False})
    assert (Path(project["directory"]) / "main.py").read_text() == "print('hi')\n"
    assert client.post(f"/api/changes/{cid}/decide", json={"approve": True}).status_code == 409  # already decided


def test_non_source_attachments_saved_under_attachments_dir(client, project):
    pid = project["id"]
    for name in ("spec.pdf", "data.csv", "notes.docx"):
        assert upload(client, pid, name, b"binary-ish").status_code == 201
    client.post(f"/api/projects/{pid}/changes/decide-all", json={"approve": True})
    root = Path(project["directory"])
    assert (root / "attachments" / "spec.pdf").is_file() and (root / "attachments" / "data.csv").is_file()
    atts = client.get(f"/api/projects/{pid}/attachments").json()
    assert {a["file_type"] for a in atts} == {"pdf", "csv", "docx"}
    assert client.get(f"/api/attachments/{atts[0]['id']}/download").status_code == 200


def test_attachment_filename_cannot_escape(client, project):
    pid = project["id"]
    r = upload(client, pid, "../../../evil.txt", b"x")
    assert r.status_code == 201
    assert [c["path"] for c in r.json()["changes"]] == ["evil.txt"]


def test_user_submit_with_file_block_and_zip(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    from app.workers.engine import Engine
    import asyncio

    asyncio.get_event_loop_policy()  # noqa
    asyncio.run(Engine().run_once())
    t1 = tasks(client, pid)[0]
    z = make_zip({"extra/helper.py": "def h(): pass\n"})
    r = client.post(f"/api/tasks/{t1['id']}/submit",
                    data={"response": "Done.\n\n### FILE: notes/plan.md\n```\n# Plan\n```"},
                    files=[("files", ("extra.zip", z))])
    assert r.status_code == 200 and r.json()["status"] == "needs_review"
    paths = {c["path"] for c in client.get(f"/api/projects/{pid}/changes").json()["changes"]}
    assert paths == {"notes/plan.md", "extra/helper.py"}
    assert client.post(f"/api/tasks/{t1['id']}/submit", data={"response": ""}).status_code in (400, 409)


# ---- conversations ---------------------------------------------------------------------------------------

def test_conversation_search_filter_export(client, project, manual_account):
    import asyncio
    from app.workers.engine import Engine

    pid = setup_project(client, project, [manual_account["id"]])
    asyncio.run(Engine().run_once())
    t1 = tasks(client, pid)[0]
    client.post(f"/api/tasks/{t1['id']}/submit", data={"response": "The flux capacitor is fine"})
    hits = client.get("/api/conversations/search", params={"q": "flux capacitor"}).json()
    assert len(hits) == 1 and hits[0]["kind"] == "ai_response" and hits[0]["account_name"] == "Claude Account 1"
    assert hits[0]["provider_name"] == "Claude" and hits[0]["task_id"] == t1["id"]
    kinds = {m["kind"] for m in client.get("/api/conversations/search", params={"project_id": pid}).json()}
    assert {"user_prompt", "ai_response", "system_event", "checkpoint"} <= kinds
    errs = client.get("/api/conversations/search", params={"kind": "error"}).json()
    assert errs == []
    cid = client.get(f"/api/conversations?project_id={pid}").json()[0]["id"]
    md = client.get(f"/api/conversations/{cid}/export", params={"format": "markdown"})
    assert "flux capacitor" in md.text and md.headers["content-type"].startswith("text/markdown")
    js = client.get(f"/api/conversations/{cid}/export", params={"format": "json"}).json()
    assert js["messages"] and js["task_id"] == t1["id"]
    assert client.get(f"/api/conversations/{cid}/export", params={"format": "xml"}).status_code == 400


def test_conversation_mirrored_to_jsonl(client, project, manual_account, home):
    import asyncio
    from app.workers.engine import Engine

    pid = setup_project(client, project, [manual_account["id"]])
    asyncio.run(Engine().run_once())
    t1 = tasks(client, pid)[0]
    f = home / "data" / "conversations" / str(pid) / f"task-{t1['id']}.jsonl"
    rows = [json.loads(l) for l in f.read_text().splitlines()]
    assert rows and rows[0]["kind"] == "user_prompt"


# ---- checkpoints --------------------------------------------------------------------------------------------

def mk_ck(client, pid, label="c"):
    r = client.post(f"/api/projects/{pid}/checkpoints", json={"label": label})
    assert r.status_code == 201
    return r.json()


def test_checkpoint_contents(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]], start=False)
    c = mk_ck(client, pid)
    full = client.get(f"/api/checkpoints/{c['id']}").json()
    st = full["state"]
    assert st["tasks"] and st["current_prompt"] == MASTER and st["file_manifest"] and "conversation_refs" in st
    assert full["git_commit"] and not any(m["path"] == ".env" for m in st["file_manifest"])


def test_restore_refuses_to_destroy_newer_changes_then_force_makes_backup(client, project):
    pid = project["id"]
    root = Path(project["directory"])
    c1 = mk_ck(client, pid, "v1")
    (root / "main.py").write_text("print('user edit after checkpoint')\n")
    (root / "extra.txt").write_text("new file\n")
    r = client.post(f"/api/checkpoints/{c1['id']}/restore", json={})
    assert r.status_code == 409
    assert set(r.json()["detail"]["changed_files"]) == {"main.py", "extra.txt"}
    assert (root / "main.py").read_text().startswith("print('user edit")  # untouched
    r = client.post(f"/api/checkpoints/{c1['id']}/restore", json={"force": True})
    assert r.status_code == 200
    out = r.json()
    assert (root / "main.py").read_text() == "print('hi')\n" and not (root / "extra.txt").exists()
    assert (root / ".env").read_text() == "SECRET=abc\n"  # protected files untouched by restore
    # the newer state was saved first and can be brought back
    pre = out["pre_restore_checkpoint"]
    assert client.post(f"/api/checkpoints/{pre}/restore", json={"force": True}).status_code == 200
    assert (root / "main.py").read_text().startswith("print('user edit")
    assert (root / "extra.txt").read_text() == "new file\n"


def test_restore_task_state(client, project, manual_account):
    import asyncio
    from app.workers.engine import Engine

    pid = setup_project(client, project, [manual_account["id"]], start=False)
    c = mk_ck(client, pid, "before work")
    client.post(f"/api/projects/{pid}/start")
    asyncio.run(Engine().run_once())
    t1 = tasks(client, pid)[0]
    client.post(f"/api/tasks/{t1['id']}/submit", data={"response": "done"})
    assert tasks(client, pid)[0]["status"] == "completed"
    r = client.post(f"/api/checkpoints/{c['id']}/restore", json={"force": True})
    assert r.status_code == 200
    assert tasks(client, pid)[0]["status"] == "ready"


def test_checkpoint_compare_export_delete(client, project):
    pid = project["id"]
    root = Path(project["directory"])
    a = mk_ck(client, pid, "a")
    (root / "main.py").write_text("print('b')\n")
    (root / "added.py").write_text("1\n")
    (root / "README.md").unlink()
    b = mk_ck(client, pid, "b")
    cmp = client.get("/api/checkpoints/compare", params={"a": a["id"], "b": b["id"]}).json()
    assert cmp["files_added"] == ["added.py"] and cmp["files_modified"] == ["main.py"] and cmp["files_removed"] == ["README.md"]
    ex = client.get(f"/api/checkpoints/{a['id']}/export")
    assert ex.status_code == 200
    import io

    z = zipfile.ZipFile(io.BytesIO(ex.content))
    assert "checkpoint.json" in z.namelist() and z.read("files/main.py") == b"print('hi')\n"
    assert not any(n.endswith(".env") for n in z.namelist())
    assert client.delete(f"/api/checkpoints/{a['id']}").status_code == 204
    assert client.get(f"/api/checkpoints/{a['id']}").status_code == 404


def test_history_and_diff(client, project):
    pid = project["id"]
    root = Path(project["directory"])
    a = mk_ck(client, pid, "a")
    (root / "main.py").write_text("print('changed')\n")
    b = mk_ck(client, pid, "b")
    hist = client.get(f"/api/projects/{pid}/history").json()
    ha, hb = (client.get(f"/api/checkpoints/{x['id']}").json()["git_commit"] for x in (a, b))
    assert hist[0]["hash"] == hb
    d = client.get(f"/api/projects/{pid}/history/diff", params={"a": ha, "b": hb}).text
    assert "-print('hi')" in d and "+print('changed')" in d
    assert client.get(f"/api/projects/{pid}/history/diff", params={"a": "--output=x", "b": hb}).status_code == 400


# ---- export ------------------------------------------------------------------------------------------------------

def test_export_contains_everything_and_no_secrets(client, project, manual_account):
    import asyncio, io
    from app.workers.engine import Engine

    pid = setup_project(client, project, [manual_account["id"]])
    asyncio.run(Engine().run_once())
    t1 = tasks(client, pid)[0]
    client.post(f"/api/tasks/{t1['id']}/submit", data={"response": "analysis text"})
    r = client.post(f"/api/projects/{pid}/export", json={"include_original": True})
    assert r.status_code == 200
    data = client.get(r.json()["download"])
    z = zipfile.ZipFile(io.BytesIO(data.content))
    names = set(z.namelist())
    for must in ("README.md", "FINAL_REPORT.md", "TASK_COMPLETION_REPORT.md", "project/main.py",
                 "handoff/HANDOFF.md", "history/checkpoints.json", "history/git-history.json", "original/main.py"):
        assert must in names, must
    assert any(n.startswith("conversations/") and n.endswith(".md") for n in names)
    assert not any(n.endswith(".env") for n in names)
    assert "analysis text" in z.read("FINAL_REPORT.md").decode()
    assert b"SECRET=abc" not in b"".join(z.read(n) for n in names)
    assert not any(".git/" in n for n in names)
    for kind in ("conversations", "history", "checkpoints", "handoff"):
        assert client.post(f"/api/projects/{pid}/export/{kind}").status_code == 200
    assert client.post(f"/api/projects/{pid}/export/bogus").status_code == 404
    assert len(client.get("/api/exports").json()) == 5
    assert client.get("/api/exports/..%2f..%2fetc%2fpasswd").status_code == 404
    assert any(n["kind"] == "export_completed" for n in client.get("/api/notifications").json())


# ---- dashboard / live updates / settings ----------------------------------------------------------------------------

def test_dashboard_counts(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    d = client.get("/api/dashboard").json()
    assert d["projects"] == 1 and d["accounts_total"] == 1 and d["accounts_automated"] == 0
    assert d["active_tasks"] >= 1 and d["engine"]["state"] in ("stopped", "running", "paused")
    assert isinstance(d["recent_activity"], list) and d["recent_activity"]


def test_websocket_reconnect_resyncs_state(client, project, manual_account):
    pid = setup_project(client, project, [manual_account["id"]])
    t1 = tasks(client, pid)[0]
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ws.send_text("ping")
        assert ws.receive_json()["type"] == "pong"
        client.post(f"/api/tasks/{t1['id']}/cancel")
        ev = ws.receive_json()
        assert ev["type"] in ("activity", "task")
    # dashboard "closed"; state keeps changing with nobody listening
    client.post(f"/api/tasks/{t1['id']}/retry")
    from app.services.events import bus

    assert bus.subscriber_count == 0
    # reconnect: new socket works and REST shows the true current state
    with client.websocket_connect("/ws") as ws2:
        assert ws2.receive_json()["type"] == "hello"
    assert tasks(client, pid)[0]["status"] == "ready"


def test_websocket_rejects_foreign_origin(client):
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example.com"}):
            pass


def test_settings_and_notification_preferences(client, project, manual_account):
    s = client.get("/api/settings").json()
    assert s["theme"] == "dark" and s["notifications.enabled"] is True
    assert client.put("/api/settings", json={"values": {"bogus": 1}}).status_code == 400
    client.put("/api/settings", json={"values": {"notifications.user_action_required": False, "theme": "light"}})
    import asyncio
    from app.workers.engine import Engine

    setup_project(client, project, [manual_account["id"]])
    asyncio.run(Engine().run_once())
    assert not any(n["kind"] == "user_action_required" for n in client.get("/api/notifications").json())
    assert client.get("/api/settings").json()["theme"] == "light"


def test_database_backup(client, home):
    r = client.post("/api/backup")
    assert r.status_code == 200
    import sqlite3

    f = home / "data" / "backups" / r.json()["file"]
    con = sqlite3.connect(f)
    assert con.execute("select count(*) from ai_providers").fetchone()[0] >= 4
    con.close()


def test_all_required_tables_exist(client, home):
    import sqlite3

    con = sqlite3.connect(home / "data" / "database" / "project_pilot.db")
    have = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    need = {"projects", "project_files", "project_prompts", "ai_providers", "ai_accounts",
            "project_provider_assignments", "tasks", "task_dependencies", "task_executions", "conversations",
            "conversation_messages", "attachments", "checkpoints", "file_changes", "activity_logs",
            "notifications", "application_settings"}
    assert need <= have
    assert con.execute("pragma foreign_keys").fetchone()[0] in (0, 1)
    con.close()
