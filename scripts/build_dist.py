"""Assemble the distributable ZIP from a PyInstaller one-folder build.

  python scripts/build_dist.py [--platform windows] [--dist build/dist/ProjectPilot] [--out release]

Output: release/ProjectPilot-<version>-<platform>.zip (+ SHA256SUMS.txt). The ZIP is deterministic for
identical inputs (sorted entries, fixed timestamps) so releases are reproducible/verifiable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXED_TIME = (2020, 1, 1, 0, 0, 0)


def version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "backend/app/__init__.py").read_text())
    if not m:
        raise SystemExit("cannot read __version__")
    return m.group(1)


def commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def add(z: zipfile.ZipFile, arc: str, data: bytes, mode: int = 0o644) -> None:
    info = zipfile.ZipInfo(arc, FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (mode | 0o100000) << 16
    z.writestr(info, data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="windows")
    ap.add_argument("--dist", default=str(ROOT / "build/dist/ProjectPilot"))
    ap.add_argument("--out", default=str(ROOT / "release"))
    a = ap.parse_args()
    dist, out = Path(a.dist), Path(a.out)
    if not dist.is_dir():
        raise SystemExit(f"build folder not found: {dist}")
    out.mkdir(parents=True, exist_ok=True)
    ver = version()
    name = f"ProjectPilot-{ver}-{a.platform}"
    zpath = out / f"{name}.zip"
    zpath.unlink(missing_ok=True)
    top = "ProjectPilot"
    with zipfile.ZipFile(zpath, "w") as z:
        for f in sorted(p for p in dist.rglob("*") if p.is_file()):
            mode = 0o755 if f.stat().st_mode & 0o111 else 0o644
            add(z, f"{name}/{top}/{f.relative_to(dist).as_posix()}", f.read_bytes(), mode)
        for f in sorted((ROOT / "packaging/windows").iterdir()):
            add(z, f"{name}/{f.name}", f.read_bytes())
        add(z, f"{name}/LICENSE-NOTICE.txt", b"See the repository for license and third-party notices.\n")
        add(z, f"{name}/build-info.json", json.dumps({"version": ver, "commit": commit(),
                                                     "platform": a.platform}, indent=2).encode())
    sums = out / "SHA256SUMS.txt"
    sums.write_text(f"{sha256(zpath)}  {zpath.name}\n")
    print(f"{zpath} ({zpath.stat().st_size/1e6:.1f} MB)\n{sums.read_text().strip()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
