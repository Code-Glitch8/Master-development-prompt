"""Fail if tracked files look like they contain credentials. Used in CI (test.yml)."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERNS = {
    "Anthropic key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "OpenAI key": re.compile(r"sk-(proj-)?[A-Za-z0-9]{32,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,}"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "Private key block": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
}
FORBIDDEN_NAMES = re.compile(r"(^|/)(\.env(\..+)?|.*\.(pem|key|pfx|p12)|.*\.db|.*\.sqlite3?)$")
ALLOWED = {".env.example"}


def main() -> int:
    files = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    bad: list[str] = []
    for rel in files:
        if Path(rel).name in ALLOWED or not (ROOT / rel).is_file():
            continue
        if FORBIDDEN_NAMES.search(rel):
            bad.append(f"{rel}: forbidden file type is tracked")
            continue
        if rel == "scripts/check_no_secrets.py" or rel.startswith("frontend/package-lock"):
            continue
        try:
            text = (ROOT / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pat in PATTERNS.items():
            if pat.search(text):
                bad.append(f"{rel}: possible {label}")
    for b in bad:
        print("SECRET-SCAN:", b)
    print(f"Scanned {len(files)} tracked files: {'FAILED' if bad else 'clean'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
