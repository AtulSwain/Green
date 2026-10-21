import os
import sys

import pytest

from green.security.path_policy import PathPolicy, PathPolicyError, is_sensitive_name
from green.services import file_access


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "approved"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "notes.txt").write_text("hello")
    (root / ".env").write_text("API_KEY=secret")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("x")
    (root / "id_rsa").write_text("PRIVATE")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret")
    return root, outside


def test_no_roots_denies_everything(tmp_path):
    with pytest.raises(PathPolicyError, match="No approved"):
        PathPolicy([]).resolve(str(tmp_path))


def test_resolves_inside(tree):
    root, _ = tree
    policy = PathPolicy([root])
    assert policy.resolve("docs/notes.txt") == (root / "docs" / "notes.txt").resolve()
    assert policy.resolve(str(root / "docs")) == (root / "docs").resolve()


@pytest.mark.parametrize("raw", ["../outside/secret.txt", "docs/../../outside/secret.txt", "docs/../.."])
def test_traversal_rejected(tree, raw):
    root, _ = tree
    with pytest.raises(PathPolicyError, match="outside"):
        PathPolicy([root]).resolve(raw)


def test_absolute_outside_rejected(tree):
    root, outside = tree
    with pytest.raises(PathPolicyError, match="outside"):
        PathPolicy([root]).resolve(str(outside / "secret.txt"))


def test_prefix_sibling_not_confused(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data-private").mkdir()
    (tmp_path / "data-private" / "x.txt").write_text("x")
    with pytest.raises(PathPolicyError):
        PathPolicy([tmp_path / "data"]).resolve(str(tmp_path / "data-private" / "x.txt"))


@pytest.mark.parametrize("raw", [".env", ".git/config", "node_modules/pkg/index.js", "id_rsa"])
def test_sensitive_files_rejected(tree, raw):
    root, _ = tree
    with pytest.raises(PathPolicyError, match="protected"):
        PathPolicy([root]).resolve(raw)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_symlink_escape_rejected(tree):
    root, outside = tree
    link = root / "shortcut"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    policy = PathPolicy([root])
    with pytest.raises(PathPolicyError, match="outside"):
        policy.resolve("shortcut/secret.txt")
    assert not policy.contains(link / "secret.txt")
    names = [p.name for p in policy.walk_files(root)]
    assert "secret.txt" not in names


@pytest.mark.parametrize("raw", ["\\\\server\\share\\file.txt", "//server/share/x", "\\\\?\\C:\\x", "a\x00b", "x" * 2000, "  "])
def test_dangerous_raw_paths_rejected(tree, raw):
    root, _ = tree
    with pytest.raises(PathPolicyError):
        PathPolicy([root]).resolve(raw)


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS only")
def test_alternate_data_stream_rejected(tree):
    root, _ = tree
    with pytest.raises(PathPolicyError):
        PathPolicy([root]).resolve(str(root / "docs" / "notes.txt:hidden"))


def test_missing_path(tree):
    root, _ = tree
    with pytest.raises(PathPolicyError, match="does not exist"):
        PathPolicy([root]).resolve("missing.txt")
    assert PathPolicy([root]).resolve("missing.txt", must_exist=False).name == "missing.txt"


def test_walk_and_list_skip_protected(tree):
    root, _ = tree
    policy = PathPolicy([root])
    walked = {p.relative_to(root.resolve()).as_posix() for p in policy.walk_files(root.resolve())}
    assert walked == {"docs/notes.txt"}
    entries, truncated = file_access.list_directory(policy, root.resolve(), 100)
    assert [e.name for e in entries] == ["docs"]
    assert not truncated


def test_sensitive_name_patterns():
    for name in [".env", ".env.local", "server.pem", "key.p12", "credentials.json", ".npmrc", "Login Data"]:
        assert is_sensitive_name(name), name
    for name in ["main.py", "environment.md", "README.md"]:
        assert not is_sensitive_name(name), name


def test_read_text_limits(tmp_path):
    big = tmp_path / "big.txt"
    big.write_text("a" * 5000)
    content = file_access.read_text(big, 1000)
    assert content.truncated and len(content.text) == 1000
    binary = tmp_path / "data.txt"
    binary.write_bytes(b"abc\x00def")
    with pytest.raises(file_access.FileAccessError, match="binary"):
        file_access.read_text(binary, 1000)
    exe = tmp_path / "prog.exe"
    exe.write_bytes(b"MZ")
    with pytest.raises(file_access.FileAccessError, match="Unsupported"):
        file_access.read_text(exe, 1000)
