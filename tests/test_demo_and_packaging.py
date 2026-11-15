"""The shipped demo and release tooling must keep working (they run in CI)."""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_demo_workflow_runs_without_any_ai_account():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_workflow.py")], capture_output=True,
                       text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "DEMO PASSED" in r.stdout and "refused, as designed" in r.stdout


def test_secret_scanner_passes_on_repo():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_no_secrets.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_dist_zip_is_deterministic_and_has_windows_scripts(tmp_path):
    fake = tmp_path / "ProjectPilot"
    (fake / "_internal").mkdir(parents=True)
    (fake / "ProjectPilot.exe").write_bytes(b"MZ-fake")
    (fake / "_internal" / "lib.dll").write_bytes(b"x")
    outs = []
    for i in (1, 2):
        out = tmp_path / f"out{i}"
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_dist.py"), "--dist", str(fake), "--out", str(out)],
                       check=True, capture_output=True)
        outs.append(next(out.glob("*.zip")))
    assert outs[0].read_bytes() == outs[1].read_bytes()
    names = zipfile.ZipFile(outs[0]).namelist()
    for n in ("install.bat", "run.bat", "uninstall.bat", "stop.bat", "README.txt", "build-info.json"):
        assert any(x.endswith("/" + n) for x in names), n
    info = json.loads(zipfile.ZipFile(outs[0]).read(next(x for x in names if x.endswith("build-info.json"))))
    assert info["platform"] == "windows"


def test_workflows_exist_and_only_use_local_token():
    import yaml

    wf = ROOT / ".github" / "workflows"
    for name in ("build-windows.yml", "test.yml", "release.yml"):
        data = yaml.safe_load((wf / name).read_text())
        assert data["jobs"], name
    text = (wf / "release.yml").read_text()
    assert "secrets.GITHUB_TOKEN" in text and "secrets." not in text.replace("secrets.GITHUB_TOKEN", "")


def test_bat_scripts_have_crlf_attribute():
    assert "*.bat text eol=crlf" in (ROOT / ".gitattributes").read_text()
