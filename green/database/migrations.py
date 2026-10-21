"""Schema creation and versioned migrations.

The schema version is stored in SQLite's ``PRAGMA user_version``. On startup:

* a database that cannot be read is moved aside (never deleted) and a new one created;
* a database from a newer GREEN version is refused without modification;
* an older database is backed up before migrations are applied in a transaction.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .connection import Database

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    statements: tuple[str, ...]


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        1,
        "initial schema",
        (
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'complete'
                    CHECK (status IN ('complete', 'cancelled', 'error')),
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_messages_conversation ON messages(conversation_id, id)",
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                category TEXT NOT NULL CHECK (category IN
                    ('preference', 'project_note', 'fact', 'instruction', 'summary')),
                tags TEXT NOT NULL DEFAULT '[]',
                source_conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_memories_category ON memories(category)",
            """
            CREATE TABLE reminders (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                due_at TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
                notified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX idx_reminders_due ON reminders(completed, due_at)",
            """
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                category TEXT NOT NULL,
                decision TEXT NOT NULL,
                outcome TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT ''
            )
            """,
        ),
    ),
)


class DatabaseInitError(Exception):
    """The database could not be opened. The message is safe to show the user."""


class IncompatibleDatabaseError(DatabaseInitError):
    pass


@dataclass
class InitResult:
    database: Database
    schema_version: int
    backup_path: Path | None = None
    quarantined_path: Path | None = None
    notices: list[str] = field(default_factory=list)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _probe(path: Path) -> int:
    """Return the schema version of an existing database, raising sqlite3.DatabaseError if unreadable."""
    conn = sqlite3.connect(path)
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise sqlite3.DatabaseError(f"integrity check failed: {result[0] if result else 'no result'}")
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def _quarantine(path: Path) -> Path:
    target = path.with_name(f"{path.name}.corrupt-{_timestamp()}")
    os.replace(path, target)
    for suffix in ("-wal", "-shm"):
        side = path.with_name(path.name + suffix)
        if side.exists():
            os.replace(side, target.with_name(target.name + suffix))
    return target


def _backup(path: Path, backup_dir: Path, version: int) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"{path.stem}-schema{version}-{_timestamp()}.db"
    source = sqlite3.connect(path)
    dest = sqlite3.connect(target)
    try:
        source.backup(dest)
    finally:
        dest.close()
        source.close()
    return target


def apply_migrations(db: Database, migrations: tuple[Migration, ...] | list[Migration]) -> int:
    current = db.schema_version()
    for migration in sorted(migrations, key=lambda m: m.version):
        if migration.version <= current:
            continue
        log.info("Applying database migration %d (%s)", migration.version, migration.description)
        with db.transaction() as conn:
            for statement in migration.statements:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {int(migration.version)}")
        current = migration.version
    return current


def initialize_database(
    path: Path,
    backup_dir: Path,
    migrations: tuple[Migration, ...] | list[Migration] = MIGRATIONS,
) -> InitResult:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    latest = max((m.version for m in migrations), default=0)
    notices: list[str] = []
    quarantined: Path | None = None
    backup: Path | None = None

    existing_version = 0
    if path.exists() and path.stat().st_size > 0:
        try:
            existing_version = _probe(path)
        except sqlite3.DatabaseError as exc:
            log.error("Database unreadable (%s); moving it aside", exc)
            quarantined = _quarantine(path)
            notices.append(
                "The existing database could not be read and was moved aside to "
                f"{quarantined}. A new, empty database was created."
            )
            existing_version = 0
        if existing_version > latest:
            raise IncompatibleDatabaseError(
                f"The database was created by a newer version of GREEN (schema {existing_version}; "
                f"this version supports up to {latest}). It has not been modified."
            )
        if 0 < existing_version < latest:
            backup = _backup(path, backup_dir, existing_version)
            notices.append(f"Database upgraded; a backup was saved to {backup}.")

    db = Database(path)
    try:
        version = apply_migrations(db, migrations)
    except sqlite3.Error as exc:
        db.close()
        raise DatabaseInitError(f"Database migration failed: {exc}") from exc
    return InitResult(db, version, backup, quarantined, notices)
