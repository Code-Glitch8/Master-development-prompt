"""Regression: a write must be committed BEFORE the 2xx response reaches the client.

FastAPI >= 0.118 runs the cleanup code of `yield` dependencies after the response is sent. If the
commit lived there, a browser could receive "201 Created" and immediately re-fetch the list from
another connection and not see the row (intermittent stale UI)."""
import asyncio
import json
import sqlite3

import pytest


def asgi_call(app, method, path, body: dict | None, on_response_start):
    raw = json.dumps(body).encode() if body is not None else b""
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
             "headers": [(b"host", b"127.0.0.1"), (b"content-type", b"application/json"),
                         (b"content-length", str(len(raw)).encode())],
             "client": ("127.0.0.1", 5000), "server": ("127.0.0.1", 8765)}
    sent = {"done": False}
    out = {}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            on_response_start()

    asyncio.run(app(scope, receive, send))
    return out


def count(db_path, table):
    con = sqlite3.connect(db_path)
    try:
        return con.execute(f"select count(*) from {table}").fetchone()[0]
    finally:
        con.close()


@pytest.mark.parametrize("path,body,table", [
    ("/api/accounts", {"provider_key": "claude", "display_name": "x"}, "ai_accounts"),
    ("/api/projects", {"name": "P"}, "projects"),
])
def test_row_is_visible_to_other_connections_when_response_starts(client, home, path, body, table):
    db = home / "data" / "database" / "project_pilot.db"
    before = count(db, table)
    seen = {}
    out = asgi_call(client.app, "POST", path, body, lambda: seen.setdefault("n", count(db, table)))
    assert out["status"] == 201
    assert seen["n"] == before + 1, "response was sent before the transaction was committed"
