"""Bounded, policy-checked file operations shared by tools and the workspace screen."""

from __future__ import annotations

import difflib
import fnmatch
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..security.path_policy import PathPolicy, is_sensitive_name
from .tool_registry import ToolError

TEXT_EXTENSIONS = frozenset(
    """.txt .md .markdown .rst .log .csv .tsv .json .jsonc .yaml .yml .toml .ini .cfg .conf .xml
    .html .htm .css .scss .less .js .jsx .ts .tsx .mjs .cjs .py .pyi .pyw .java .kt .kts .c .h .cpp
    .hpp .cc .cxx .cs .go .rs .rb .php .swift .m .mm .sql .sh .bash .zsh .ps1 .psm1 .bat .cmd .lua .r
    .dart .vue .svelte .gradle .properties .tex .proto .graphql .vb .fs .scala .pl .ex .exs .erl .hs
    .clj .el .vim .cmake .mk .gitignore .gitattributes .editorconfig .csproj .sln .vcxproj""".split()
)
TEXT_FILENAMES = frozenset({"dockerfile", "makefile", "readme", "license", "changelog", "procfile", "cmakelists.txt"})
MAX_WRITE_BYTES = 1_000_000


class FileAccessError(ToolError):
    pass


def is_supported_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS or path.name.lower() in TEXT_FILENAMES


@dataclass
class TextContent:
    path: Path
    text: str
    size: int
    truncated: bool


def read_text(path: Path, max_bytes: int) -> TextContent:
    if not path.is_file():
        raise FileAccessError("Not a file.")
    if not is_supported_text(path):
        raise FileAccessError(f"Unsupported file type '{path.suffix or path.name}'. Only text and source files can be read.")
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)  # never more than the limit is loaded
    except PermissionError as exc:
        raise FileAccessError("Access to this file was denied by the operating system.") from exc
    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]
    if b"\x00" in raw[:8192]:
        raise FileAccessError("The file looks like binary data and cannot be read as text.")
    text = raw.decode("utf-8", errors="replace")
    return TextContent(path, text, size, truncated)


@dataclass
class DirEntry:
    name: str
    is_dir: bool
    size: int


def list_directory(policy: PathPolicy, directory: Path, limit: int) -> tuple[list[DirEntry], bool]:
    if not directory.is_dir():
        raise FileAccessError("Not a directory.")
    entries: list[DirEntry] = []
    truncated = False
    try:
        scanner = os.scandir(directory)
    except PermissionError as exc:
        raise FileAccessError("Access to this folder was denied by the operating system.") from exc
    except FileNotFoundError as exc:
        raise FileAccessError("The folder does not exist.") from exc
    with scanner as it:
        for entry in sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
            path = Path(entry.path)
            if not policy.contains(path):
                continue  # protected names and links pointing outside the approved area
            if len(entries) >= limit:
                truncated = True
                break
            try:
                is_dir = entry.is_dir()
                size = 0 if is_dir else entry.stat().st_size
            except OSError:
                continue
            entries.append(DirEntry(entry.name, is_dir, size))
    return entries, truncated


def _matches(name: str, pattern: str) -> bool:
    name_l, pattern_l = name.lower(), pattern.lower()
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(name_l, pattern_l)
    return pattern_l in name_l


def find_files(policy: PathPolicy, start: Path, pattern: str, limit: int) -> tuple[list[Path], bool]:
    if not start.is_dir():
        raise FileAccessError("Not a directory.")
    results: list[Path] = []
    for path in policy.walk_files(start):
        if _matches(path.name, pattern):
            if len(results) >= limit:
                return results, True
            results.append(path)
    return results, False


@dataclass
class TextMatch:
    path: Path
    line_number: int
    line: str


def search_text(
    policy: PathPolicy,
    start: Path,
    query: str,
    limit: int,
    *,
    file_pattern: str = "",
    max_file_bytes: int = 1_000_000,
) -> tuple[list[TextMatch], bool]:
    if not query:
        raise FileAccessError("Search text is required.")
    needle = query.lower()
    matches: list[TextMatch] = []
    for path in policy.walk_files(start):
        if not is_supported_text(path) or (file_pattern and not _matches(path.name, file_pattern)):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, start=1):
                    if needle in line.lower():
                        if len(matches) >= limit:
                            return matches, True
                        matches.append(TextMatch(path, number, line.rstrip("\n")[:300]))
        except OSError:
            continue
    return matches, False


def unified_diff(old: str, new: str, label: str) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    )
    text = "".join(line if line.endswith("\n") else line + "\n" for line in diff)
    return text or "(no changes)\n"


def write_new_text_file(path: Path, content: str) -> None:
    """Create a new file; never overwrites an existing one."""
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise FileAccessError("Content is too large to write.")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
    except FileExistsError as exc:
        raise FileAccessError("A file with that name already exists; GREEN will not overwrite it.") from exc


def replace_text_file(path: Path, content: str, backup_dir: Path) -> Path | None:
    """Atomically write ``content`` to ``path``; returns the backup of the previous version."""
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE_BYTES:
        raise FileAccessError("Content is too large to write.")
    backup: Path | None = None
    if path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = backup_dir / f"{stamp}-{path.name}"
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".green-tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return backup


def check_writable_name(path: Path) -> None:
    if is_sensitive_name(path.name):
        raise FileAccessError("That file name is protected.")
    if not is_supported_text(path):
        raise FileAccessError("Only text and source files can be written.")
