"""Smoke-test a built Project Pilot executable.

Starts the binary headless with a throw-away data folder, then checks: health endpoint, dashboard
served, a project can be created through the API, the SQLite DB lives in the data folder, and the
service shuts down. Usage: python scripts/smoke_test.py path/to/ProjectPilot[.exe]
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(url: str, data: dict | None = None):
    req = urllib.request.Request(url, method="POST" if data is not None else "GET",
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 (localhost only)
        return r.status, r.read()


def main() -> int:
    exe = Path(sys.argv[1]).resolve()
    if not exe.is_file():
        print(f"FAIL: executable not found: {exe}")
        return 2
    home = Path(tempfile.mkdtemp(prefix="pp-smoke-"))
    port = free_port()
    env = dict(os.environ, PROJECT_PILOT_HOME=str(home))
    proc = subprocess.Popen([str(exe), "--no-browser", "--port", str(port)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        health = None
        for _ in range(120):
            if proc.poll() is not None:
                print("FAIL: process exited early:\n" + (proc.stdout.read() if proc.stdout else ""))
                return 1
            try:
                health = json.loads(call(base + "/api/health")[1])
                break
            except Exception:
                time.sleep(0.5)
        assert health and health["status"] == "ok", "service did not become healthy"
        print("health:", health)
        status, body = call(base + "/")
        assert status == 200 and b"<div id=\"root\"" in body, "dashboard not served"
        status, body = call(base + "/api/projects", {"name": "Smoke Test"})
        assert status == 201, "project creation failed"
        assert any((home / "data").rglob("project_pilot.db")), \
            "database not in data folder"
        assert len(json.loads(call(base + "/api/providers")[1])) >= 4, "providers not seeded"
        print("OK: smoke test passed; data folder:", home / "data")
        return 0
    except AssertionError as exc:
        print("FAIL:", exc)
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
