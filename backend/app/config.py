"""Runtime configuration. All data lives on the user's machine."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _base_dir() -> Path:
    """Directory that holds the `data/` folder.

    - PROJECT_PILOT_HOME env var wins (used by tests and custom installs).
    - Frozen (PyInstaller) build: folder containing the executable.
    - Dev: repository root.
    """
    env = os.environ.get("PROJECT_PILOT_HOME")
    if env:
        return Path(env).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    base_dir: Path = field(default_factory=_base_dir)
    host: str = "127.0.0.1"  # localhost-only by default
    port: int = int(os.environ.get("PROJECT_PILOT_PORT", "8765"))
    max_archive_bytes: int = 2 * 1024**3  # 2 GiB uncompressed limit
    max_archive_files: int = 200_000
    max_nested_archive_depth: int = 2
    max_compression_ratio: int = 200
    default_max_retries: int = 3
    default_backoff_base_seconds: float = 2.0
    worker_poll_seconds: float = 1.0

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def db_dir(self) -> Path:
        return self.data_dir / "database"

    @property
    def db_path(self) -> Path:
        return self.db_dir / "project_pilot.db"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def conversations_dir(self) -> Path:
        return self.data_dir / "conversations"

    @property
    def checkpoints_dir(self) -> Path:
        return self.data_dir / "checkpoints"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def attachments_dir(self) -> Path:
        return self.data_dir / "attachments"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def frontend_dist(self) -> Path:
        if getattr(sys, "frozen", False):
            meipass = Path(getattr(sys, "_MEIPASS", self.base_dir))
            return meipass / "frontend_dist"
        return self.base_dir / "frontend" / "dist"

    def ensure_dirs(self) -> None:
        for d in (
            self.db_dir,
            self.projects_dir,
            self.conversations_dir,
            self.checkpoints_dir,
            self.exports_dir,
            self.backups_dir,
            self.attachments_dir,
            self.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Re-read environment (used by tests)."""
    global _settings
    _settings = None
