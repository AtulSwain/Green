"""Filesystem access policy: canonicalization, containment and sensitive-file exclusion.

Every tool that touches the filesystem must resolve user- or model-supplied paths
through :class:`PathPolicy`. Paths are canonicalized with symlinks resolved, so a
link inside an approved directory that points elsewhere is rejected.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Iterator
from pathlib import Path

SENSITIVE_DIR_NAMES = frozenset(
    {
        ".git", ".svn", ".hg", "node_modules", ".venv", "venv", "__pycache__", ".ssh", ".aws",
        ".azure", ".gnupg", ".kube", ".docker", ".mypy_cache", ".pytest_cache", ".tox",
        "site-packages", ".idea", ".vs",
    }
)

SENSITIVE_FILE_PATTERNS = (
    ".env", ".env.*", "*.env", "*.pem", "*.key", "*.p12", "*.pfx", "*.crt", "*.cer", "*.jks",
    "*.keystore", "*.kdbx", "*.ppk", "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    "credentials", "credentials.*", "*.credentials", "secrets.*", "secret.*", ".npmrc", ".pypirc",
    ".netrc", "_netrc", ".git-credentials", ".htpasswd", "known_hosts", "authorized_keys",
    "cookies", "cookies.sqlite", "login data", "web data", "key4.db", "logins.json",
)

MAX_PATH_LENGTH = 1024


class PathPolicyError(Exception):
    """A path was rejected by the policy. The message is safe to show the user."""


def is_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    if lowered in SENSITIVE_DIR_NAMES:
        return True
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in SENSITIVE_FILE_PATTERNS)


def _check_raw(raw: str) -> None:
    if not isinstance(raw, str) or not raw.strip():
        raise PathPolicyError("A path is required.")
    if "\x00" in raw:
        raise PathPolicyError("Path contains invalid characters.")
    if len(raw) > MAX_PATH_LENGTH:
        raise PathPolicyError("Path is too long.")
    normalized = raw.replace("/", "\\")
    if normalized.startswith("\\\\"):
        # UNC shares and device paths (\\?\, \\.\) are never allowed.
        raise PathPolicyError("Network and device paths are not allowed.")
    if os.name == "nt" and ":" in raw[2:]:
        # NTFS alternate data streams (file.txt:stream)
        raise PathPolicyError("Alternate data stream paths are not allowed.")


class PathPolicy:
    def __init__(self, roots: Iterable[str | os.PathLike[str]], *, exclude_sensitive: bool = True):
        resolved: list[Path] = []
        for root in roots:
            if not root:
                continue
            path = Path(root).expanduser()
            if not path.is_absolute():
                continue
            path = path.resolve(strict=False)
            if path not in resolved:
                resolved.append(path)
        self.roots = resolved
        self.exclude_sensitive = exclude_sensitive

    def _root_for(self, resolved: Path) -> Path | None:
        for root in self.roots:
            if resolved == root or resolved.is_relative_to(root):
                return root
        return None

    def contains(self, path: Path) -> bool:
        """True when ``path`` (after resolving symlinks) is inside a root and not sensitive."""
        try:
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError):
            return False
        root = self._root_for(resolved)
        if root is None:
            return False
        return not (self.exclude_sensitive and self._sensitive_part(resolved, root))

    def _sensitive_part(self, resolved: Path, root: Path) -> str | None:
        for part in resolved.relative_to(root).parts:
            if is_sensitive_name(part):
                return part
        return None

    def resolve(self, raw: str, *, must_exist: bool = True, base: Path | None = None) -> Path:
        if not self.roots:
            raise PathPolicyError("No approved directories are configured for this tool.")
        _check_raw(raw)
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (base or self.roots[0]) / candidate
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:  # e.g. symlink loops
            raise PathPolicyError("Path could not be resolved.") from exc
        root = self._root_for(resolved)
        if root is None:
            raise PathPolicyError("Access denied: the path is outside the approved directories.")
        if self.exclude_sensitive:
            part = self._sensitive_part(resolved, root)
            if part:
                raise PathPolicyError(f"Access denied: '{part}' is a protected file or folder.")
        if must_exist and not resolved.exists():
            raise PathPolicyError("The path does not exist.")
        return resolved

    def relative_display(self, path: Path) -> str:
        root = self._root_for(path)
        if root is None:
            return str(path)
        rel = path.relative_to(root)
        return str(rel) if str(rel) != "." else str(root)

    def walk_files(self, start: Path, *, max_entries: int = 50_000) -> Iterator[Path]:
        """Yield files under ``start`` without following links or entering protected folders."""
        visited = 0
        for current, dirnames, filenames in os.walk(start, followlinks=False):
            dirnames[:] = sorted(
                d for d in dirnames
                if not (self.exclude_sensitive and is_sensitive_name(d))
                and not os.path.islink(os.path.join(current, d))
            )
            for name in sorted(filenames):
                visited += 1
                if visited > max_entries:
                    return
                path = Path(current, name)
                if self.contains(path):
                    yield path
