import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolated PROJECT_PILOT_HOME with a fresh database for every test."""
    monkeypatch.setenv("PROJECT_PILOT_HOME", str(tmp_path / "home"))
    from app import config, database

    database.dispose_engine()
    config.reset_settings()
    yield tmp_path / "home"
    database.dispose_engine()
    config.reset_settings()


@pytest.fixture()
def client(home):
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app(no_engine=True)
    with TestClient(app) as c:
        yield c


def make_zip(files: dict[str, str | bytes], wrap: str | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(f"{wrap}/{name}" if wrap else name, content)
    return buf.getvalue()


SAMPLE_PROJECT = {
    "package.json": '{"name":"demo","dependencies":{"react":"18.0.0","vite":"5.0.0"},"devDependencies":{"typescript":"5.0.0"}}',
    "src/App.tsx": "export default function App(){return <div>hi</div>}\n",
    "src/auth/login.ts": "export const login = () => true\n",
    "src/styles/dashboard.css": "body{margin:0}\n",
    "tests/app.test.ts": "test('x',()=>{})\n",
    "README.md": "# Demo\n",
    "requirements.txt": "fastapi==0.110\npytest\n",
    "main.py": "print('hi')\n",
    ".env": "SECRET=abc\n",
}


@pytest.fixture()
def sample_zip():
    return make_zip(SAMPLE_PROJECT, wrap="demo-app")


@pytest.fixture()
def project(client, sample_zip):
    r = client.post("/api/projects/import/zip", files={"file": ("demo.zip", sample_zip, "application/zip")})
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture()
def manual_account(client):
    r = client.post("/api/accounts", json={"provider_key": "claude", "display_name": "Claude Account 1"})
    assert r.status_code == 201, r.text
    return r.json()
