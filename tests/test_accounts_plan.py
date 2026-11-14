import pytest

from app.tasks.planner import split_requests, classify, build_plan

MASTER = ("Completely redesign the dashboard, improve mobile responsiveness, fix authentication errors, "
          "optimize performance, and test the entire application.")


def mk_account(client, provider="claude", name="Acc", **kw):
    r = client.post("/api/accounts", json={"provider_key": provider, "display_name": name, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def test_multiple_accounts_same_provider(client):
    for i in (1, 2, 3):
        mk_account(client, "claude", f"Claude Account {i}")
    mk_account(client, "openai", "ChatGPT Account 1")
    mk_account(client, "gemini", "Gemini Account 1")
    accts = client.get("/api/accounts").json()
    assert len(accts) == 5
    assert sum(a["provider_key"] == "claude" for a in accts) == 3


def test_web_account_is_user_assisted_never_automated(client):
    a = mk_account(client)
    assert a["execution_mode"] == "user_assisted"
    assert a["execution_mode_label"] == "User-assisted execution"
    assert a["has_credential"] is False
    manual = mk_account(client, "gemini", "m", connection_type="manual_only")
    assert manual["execution_mode"] == "manual_only"


def test_api_key_account_needs_complete_config_for_automation(client, monkeypatch):
    a = mk_account(client, connection_type="api_key")
    assert a["execution_mode"] == "user_assisted"  # incomplete: no model/key => not claimed as automated
    monkeypatch.setenv("PP_TEST_KEY", "sk-test")
    r = client.patch(f"/api/accounts/{a['id']}", json={"model": "some-model", "api_key_env": "PP_TEST_KEY"})
    assert r.json()["execution_mode"] == "automated" and r.json()["has_credential"] is True
    assert "sk-test" not in r.text
    v = client.post(f"/api/accounts/{a['id']}/verify").json()
    assert v["ok"] and v["account"]["last_verified_at"]


def test_custom_provider_cannot_be_automated(client):
    r = client.post("/api/accounts", json={"provider_key": "other", "display_name": "x", "connection_type": "api_key"})
    assert r.status_code == 400


def test_secret_never_stored_in_database(client, home, monkeypatch):
    stored = {}
    from app.api import accounts as accounts_api

    monkeypatch.setattr(accounts_api, "store_secret", lambda ref, s: stored.setdefault(ref, s) is not None)
    a = mk_account(client, connection_type="api_key", model="m", api_key="sk-super-secret-123")
    assert stored  # went to the keyring hook
    import sqlite3

    raw = open(home / "data" / "database" / "project_pilot.db", "rb").read()
    assert b"sk-super-secret-123" not in raw
    assert "sk-super-secret-123" not in str(client.get(f"/api/accounts/{a['id']}").json())


def test_no_keyring_refuses_to_save_key(client, monkeypatch):
    from app.api import accounts as accounts_api

    monkeypatch.setattr(accounts_api, "store_secret", lambda ref, s: False)
    r = client.post("/api/accounts", json={"provider_key": "claude", "display_name": "x",
                                           "connection_type": "api_key", "api_key": "k"})
    assert r.status_code == 400 and "NOT saved" in r.text


def test_project_specific_providers(client, project):
    a1, a2 = mk_account(client, name="A1"), mk_account(client, "gemini", "G1")
    p2 = client.post("/api/projects", json={"name": "Other"}).json()
    client.put(f"/api/projects/{project['id']}/providers", json={"account_ids": [a1["id"], a2["id"]]})
    client.put(f"/api/projects/{p2['id']}/providers", json={"account_ids": [a2["id"]]})
    assert len(client.get(f"/api/projects/{project['id']}/providers").json()["account_ids"]) == 2
    assert client.get(f"/api/projects/{p2['id']}/providers").json()["account_ids"] == [a2["id"]]
    assert client.get(f"/api/accounts/{a2['id']}").json()["assigned_project_ids"] == [project["id"], p2["id"]]
    assert client.put(f"/api/projects/{p2['id']}/providers", json={"account_ids": [999]}).status_code == 400


def test_split_requests_master_prompt():
    items = split_requests(MASTER)
    assert len(items) >= 4
    assert any("mobile" in i.lower() for i in items)


def test_split_bullets():
    assert split_requests("Goals:\n- fix login\n- add tests\n1) update docs") == ["fix login", "add tests", "update docs"]


def test_classification():
    assert classify("fix authentication errors") == "auth"
    assert classify("fix the crash on startup") == "bugs"
    assert classify("improve mobile responsiveness") == "mobile"
    assert classify("optimize performance") == "performance"


def test_plan_structure_and_dependencies():
    analysis, tasks = build_plan(MASTER, {"git": {"is_repo": False}}, ["src/auth/login.ts", "src/styles/dashboard.css"])
    keys = {t.key for t in tasks}
    assert tasks[0].title == "Analyse project structure" and tasks[-1].title == "Generate final report"
    for t in tasks:
        assert set(t.depends_on) <= keys
    assert analysis["risks"] and analysis["required_changes"] and analysis["testing_requirements"]
    assert any("auth" in f for f in analysis["relevant_files"])


def test_plan_requires_prompt(client, project):
    assert client.post(f"/api/projects/{project['id']}/plan", json={}).status_code == 400


def full_plan(client, project, account_ids=()):
    pid = project["id"]
    client.post(f"/api/projects/{pid}/prompts", json={"title": "m", "body": MASTER, "is_draft": False})
    r = client.post(f"/api/projects/{pid}/plan", json={})
    assert r.status_code == 200, r.text
    if account_ids:
        client.put(f"/api/projects/{pid}/providers", json={"account_ids": list(account_ids)})
    return r.json()


def test_plan_generated_but_not_started_before_approval(client, project, manual_account):
    plan = full_plan(client, project, [manual_account["id"]])
    assert plan["plan_status"] == "planned" and len(plan["tasks"]) >= 6
    assert client.post(f"/api/projects/{project['id']}/start").status_code == 409  # approval required
    ts = client.get(f"/api/projects/{project['id']}/tasks").json()
    assert all(t["status"] == "pending" for t in ts)  # nothing ready until approved


def test_approve_makes_only_dependency_free_tasks_ready(client, project, manual_account):
    full_plan(client, project, [manual_account["id"]])
    assert client.post(f"/api/projects/{project['id']}/plan/approve").status_code == 200
    ts = client.get(f"/api/projects/{project['id']}/tasks").json()
    ready = [t for t in ts if t["status"] == "ready"]
    assert [t["title"] for t in ready] == ["Analyse project structure"]
    assert all(t["assigned_account_id"] == manual_account["id"] for t in ts)  # visible default assignment


def test_auto_approve_setting(client, project, manual_account):
    client.patch(f"/api/projects/{project['id']}", json={"auto_approve_plan": True})
    plan = full_plan(client, project, [manual_account["id"]])
    assert plan["plan_status"] == "approved"


def test_task_crud_reorder_and_cycle_detection(client, project, manual_account):
    full_plan(client, project, [manual_account["id"]])
    pid = project["id"]
    ts = client.get(f"/api/projects/{pid}/tasks").json()
    t1, t2 = ts[0], ts[1]
    new = client.post(f"/api/projects/{pid}/tasks", json={"title": "Extra", "depends_on": [t2["id"]]}).json()
    assert new["depends_on"] == [t2["id"]]
    # cycle: t1 depends on new (new -> t2 -> t1)
    assert client.patch(f"/api/tasks/{t1['id']}", json={"depends_on": [new["id"]]}).status_code == 409
    assert client.patch(f"/api/tasks/{new['id']}", json={"depends_on": [new["id"]]}).status_code == 409
    upd = client.patch(f"/api/tasks/{new['id']}", json={"title": "Renamed", "priority": 1}).json()
    assert upd["title"] == "Renamed" and upd["priority"] == 1
    order = [t["id"] for t in reversed(client.get(f"/api/projects/{pid}/tasks").json())]
    assert [t["id"] for t in client.post(f"/api/projects/{pid}/tasks/reorder", json={"ordered_ids": order}).json()] == order
    assert client.post(f"/api/projects/{pid}/tasks/reorder", json={"ordered_ids": order[:-1]}).status_code == 409
    assert client.delete(f"/api/tasks/{new['id']}").status_code == 204
    g = client.get(f"/api/projects/{pid}/graph").json()
    assert g["nodes"] and g["edges"]


def test_assignment_only_to_project_accounts(client, project, manual_account):
    full_plan(client, project, [manual_account["id"]])
    other = mk_account(client, "gemini", "Not in project")
    t = client.get(f"/api/projects/{project['id']}/tasks").json()[0]
    assert client.post(f"/api/tasks/{t['id']}/assign", json={"account_id": other["id"]}).status_code == 409
    assert client.post(f"/api/tasks/{t['id']}/assign", json={"account_id": manual_account["id"]}).status_code == 200


def test_removing_account_from_project_unassigns_tasks_visibly(client, project, manual_account):
    full_plan(client, project, [manual_account["id"]])
    client.post(f"/api/projects/{project['id']}/plan/approve")
    client.put(f"/api/projects/{project['id']}/providers", json={"account_ids": []})
    assert all(t["assigned_account_id"] is None for t in client.get(f"/api/projects/{project['id']}/tasks").json())
