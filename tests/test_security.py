import io
import os
import stat
import zipfile

import pytest

from app.utils.paths import UnsafePathError, is_protected, normalize_rel, safe_join
from app.utils.safe_zip import ArchiveError, extract_zip, inspect_zip
from conftest import make_zip


def write(tmp_path, data: bytes):
    p = tmp_path / "a.zip"
    p.write_bytes(data)
    return p


@pytest.mark.parametrize("bad", ["../evil.txt", "a/../../evil.txt", "/etc/passwd", "C:/Windows/x", "\\\\server\\share\\x",
                                 "a/b\x00c", ""])
def test_normalize_rejects_unsafe(bad):
    with pytest.raises(UnsafePathError):
        normalize_rel(bad)


def test_normalize_ok():
    assert normalize_rel("a\\b/./c.txt") == "a/b/c.txt"


def test_safe_join_stays_inside(tmp_path):
    assert safe_join(tmp_path, "x/y.txt").is_relative_to(tmp_path.resolve())
    with pytest.raises(UnsafePathError):
        safe_join(tmp_path, "../x")


def test_zip_path_traversal_rejected(tmp_path):
    p = write(tmp_path, make_zip({"../../evil.txt": "x"}))
    with pytest.raises(ArchiveError):
        extract_zip(p, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_zip_absolute_path_rejected(tmp_path):
    p = write(tmp_path, make_zip({"/abs/evil.txt": "x"}))
    with pytest.raises(ArchiveError):
        inspect_zip(p)


def test_zip_symlink_skipped(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(info, "/etc/passwd")
        z.writestr("ok.txt", "fine")
    p = write(tmp_path, buf.getvalue())
    rep = extract_zip(p, tmp_path / "out")
    assert rep.files == ["ok.txt"]
    assert not (tmp_path / "out" / "link").exists()
    assert any("symlink" in w for w in rep.warnings)


def test_zip_duplicate_names_case_insensitive(tmp_path):
    p = write(tmp_path, make_zip({"A.txt": "1", "a.txt": "2"}))
    rep = extract_zip(p, tmp_path / "out")
    assert len(rep.files) == 2
    assert rep.renamed
    contents = sorted((tmp_path / "out" / f).read_text() for f in rep.files)
    assert contents == ["1", "2"]


def test_zip_nested_archive_not_extracted(tmp_path):
    inner = make_zip({"deep.txt": "x"})
    p = write(tmp_path, make_zip({"inner.zip": inner, "a.txt": "a"}))
    rep = extract_zip(p, tmp_path / "out")
    assert rep.nested_archives == ["inner.zip"]
    assert (tmp_path / "out" / "inner.zip").is_file()
    assert not (tmp_path / "out" / "deep.txt").exists()


def test_zip_too_many_entries(tmp_path, home):
    from app.config import get_settings

    get_settings().max_archive_files = 3
    p = write(tmp_path, make_zip({f"f{i}.txt": "x" for i in range(5)}))
    with pytest.raises(ArchiveError, match="too many"):
        inspect_zip(p)


def test_zip_size_limit(tmp_path, home):
    from app.config import get_settings

    get_settings().max_archive_bytes = 100
    p = write(tmp_path, make_zip({"big.txt": "x" * 1000}))
    with pytest.raises(ArchiveError, match="too large"):
        inspect_zip(p)


def test_not_a_zip(tmp_path):
    with pytest.raises(ArchiveError):
        inspect_zip(write(tmp_path, b"nope"))


def test_strip_single_root(tmp_path):
    p = write(tmp_path, make_zip({"a.txt": "1", "d/b.txt": "2"}, wrap="root"))
    rep = extract_zip(p, tmp_path / "out")
    assert rep.root_prefix == "root"
    assert (tmp_path / "out" / "a.txt").exists() and (tmp_path / "out" / "d" / "b.txt").exists()


@pytest.mark.parametrize("name,expected", [(".env", True), (".env.local", True), ("server.pem", True),
                                           ("id_rsa", True), ("app.sqlite", True), (".env.example", False),
                                           ("main.py", False), ("credentials.json", True)])
def test_protected_files(name, expected):
    assert is_protected(name) is expected


def test_localhost_only_host_header(client):
    assert client.get("/api/health").status_code == 200
    r = client.get("/api/health", headers={"host": "evil.example.com"})
    assert r.status_code == 403
    r = client.post("/api/projects", json={"name": "x"}, headers={"origin": "https://evil.example.com"})
    assert r.status_code == 403


def test_default_bind_is_loopback(home):
    from app.config import get_settings

    assert get_settings().host == "127.0.0.1"
