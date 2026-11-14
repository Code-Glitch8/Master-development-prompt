"""Hardened ZIP inspection and extraction.

Guards against: path traversal / absolute paths, symlink entries, zip bombs
(total size, file count, per-entry compression ratio), duplicate names
(case-insensitive collisions), and nested archives (depth-limited, never
extracted recursively by default).
"""
from __future__ import annotations

import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..config import get_settings
from .paths import UnsafePathError, normalize_rel, safe_join

ARCHIVE_SUFFIXES = (".zip",)


class ArchiveError(ValueError):
    pass


@dataclass
class ArchiveReport:
    root_prefix: str = ""  # single top-level directory that was stripped, if any
    files: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    nested_archives: list[str] = field(default_factory=list)
    total_bytes: int = 0
    renamed: dict[str, str] = field(default_factory=dict)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode) if mode else False


def _detect_root_prefix(names: list[str]) -> str:
    tops = {PurePosixPath(n).parts[0] for n in names if PurePosixPath(n).parts}
    if len(tops) == 1:
        top = next(iter(tops))
        # only strip if every entry lives *inside* that directory
        if all(len(PurePosixPath(n).parts) > 1 for n in names):
            return top
    return ""


def inspect_zip(zip_path: Path) -> ArchiveReport:
    """Validate an archive without extracting it. Raises ArchiveError if unsafe."""
    s = get_settings()
    report = ArchiveReport()
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"not a valid ZIP archive: {exc}") from exc
    with zf:
        infos = zf.infolist()
        if len(infos) > s.max_archive_files:
            raise ArchiveError(f"archive has too many entries ({len(infos)} > {s.max_archive_files})")
        total = 0
        good: list[str] = []
        for info in infos:
            name = info.filename
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                raise ArchiveError("encrypted archives are not supported")
            try:
                rel = normalize_rel(name)
            except UnsafePathError as exc:
                raise ArchiveError(f"unsafe entry in archive: {exc}") from exc
            if _is_symlink(info):
                report.skipped.append(rel)
                report.warnings.append(f"skipped symlink entry: {rel}")
                continue
            total += info.file_size
            if total > s.max_archive_bytes:
                raise ArchiveError("archive is too large when uncompressed")
            if info.compress_size > 0 and info.file_size / max(info.compress_size, 1) > s.max_compression_ratio \
                    and info.file_size > 10 * 1024 * 1024:
                raise ArchiveError(f"suspicious compression ratio for entry: {rel}")
            good.append(rel)
            if rel.lower().endswith(ARCHIVE_SUFFIXES):
                report.nested_archives.append(rel)
        report.total_bytes = total
        report.files = good
        report.root_prefix = _detect_root_prefix(good)
    return report


def extract_zip(zip_path: Path, dest: Path, strip_root: bool = True) -> ArchiveReport:
    """Safely extract `zip_path` into `dest` (created if needed).

    Nested archives are left as-is (never auto-extracted) and reported.
    Case-insensitive duplicate names are kept, the later one renamed.
    """
    report = inspect_zip(zip_path)
    dest.mkdir(parents=True, exist_ok=True)
    prefix = report.root_prefix if strip_root else ""
    seen: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = normalize_rel(info.filename)
            if rel not in report.files:  # skipped (symlink etc.)
                continue
            if prefix and rel.startswith(prefix + "/"):
                rel = rel[len(prefix) + 1 :]
            key = rel.lower()
            if key in seen:
                p = PurePosixPath(rel)
                n = 2
                while True:
                    cand = str(p.with_name(f"{p.stem} ({n}){p.suffix}"))
                    if cand.lower() not in seen:
                        break
                    n += 1
                report.warnings.append(f"duplicate filename {rel!r} saved as {cand!r}")
                report.renamed[rel] = cand
                rel = cand
                key = rel.lower()
            seen[key] = rel
            target = safe_join(dest, rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
    if report.nested_archives:
        report.warnings.append(
            f"{len(report.nested_archives)} nested archive(s) were kept as files and not extracted"
        )
    report.files = sorted(seen.values())
    return report
