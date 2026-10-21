import sqlite3

import pytest

from green.database.migrations import (
    MIGRATIONS,
    IncompatibleDatabaseError,
    Migration,
    DatabaseInitError,
    initialize_database,
)
from green.services.conversation_service import ConversationService

LATEST = max(m.version for m in MIGRATIONS)


def test_fresh_database_initialised(tmp_path):
    result = initialize_database(tmp_path / "green.db", tmp_path / "backups")
    db = result.database
    assert result.schema_version == LATEST
    tables = {r[0] for r in db.query_all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"conversations", "messages", "memories", "reminders", "audit_log"} <= tables
    assert db.query_one("PRAGMA foreign_keys")[0] == 1
    assert result.backup_path is None and not result.notices
    db.close()


def test_existing_data_preserved_on_restart(tmp_path):
    path = tmp_path / "green.db"
    first = initialize_database(path, tmp_path / "b")
    ConversationService(first.database).create("Keep me")
    first.database.close()
    second = initialize_database(path, tmp_path / "b")
    titles = [c.title for c in ConversationService(second.database).list()]
    assert titles == ["Keep me"]
    assert not list((tmp_path / "b").glob("*")) if (tmp_path / "b").exists() else True
    second.database.close()


def test_corrupt_database_is_moved_aside_not_deleted(tmp_path):
    path = tmp_path / "green.db"
    path.write_bytes(b"this is definitely not sqlite" * 100)
    result = initialize_database(path, tmp_path / "b")
    assert result.quarantined_path is not None and result.quarantined_path.exists()
    assert result.quarantined_path.read_bytes().startswith(b"this is definitely")
    assert result.notices and "moved aside" in result.notices[0]
    assert result.schema_version == LATEST
    result.database.close()


def test_newer_database_refused_and_untouched(tmp_path):
    path = tmp_path / "green.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE future (x)")
    conn.execute(f"PRAGMA user_version = {LATEST + 5}")
    conn.commit()
    conn.close()
    before = path.read_bytes()
    with pytest.raises(IncompatibleDatabaseError, match="newer version"):
        initialize_database(path, tmp_path / "b")
    assert path.read_bytes() == before


def test_upgrade_creates_backup_and_applies_migration(tmp_path):
    path = tmp_path / "green.db"
    initialize_database(path, tmp_path / "b").database.close()
    extra = Migration(LATEST + 1, "add column", ("ALTER TABLE conversations ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",))
    result = initialize_database(path, tmp_path / "b", (*MIGRATIONS, extra))
    assert result.schema_version == LATEST + 1
    assert result.backup_path is not None and result.backup_path.exists()
    backup = sqlite3.connect(result.backup_path)
    assert backup.execute("PRAGMA user_version").fetchone()[0] == LATEST
    backup.close()
    cols = {r[1] for r in result.database.query_all("PRAGMA table_info(conversations)")}
    assert "pinned" in cols
    result.database.close()


def test_failed_migration_rolls_back(tmp_path):
    path = tmp_path / "green.db"
    initialize_database(path, tmp_path / "b").database.close()
    broken = Migration(
        LATEST + 1, "broken", ("CREATE TABLE half_done (x)", "THIS IS NOT SQL")
    )
    with pytest.raises(DatabaseInitError):
        initialize_database(path, tmp_path / "b", (*MIGRATIONS, broken))
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == LATEST
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "half_done" not in tables
    conn.close()


def test_queries_are_parameterised(tmp_path):
    db = initialize_database(tmp_path / "green.db", tmp_path / "b").database
    service = ConversationService(db)
    nasty = "x'); DROP TABLE conversations; --"
    conv = service.create(nasty)
    assert service.get(conv.id).title == nasty
    assert db.query_one("SELECT COUNT(*) FROM conversations")[0] == 1
    db.close()


def test_database_usable_from_worker_thread(tmp_path):
    import threading

    db = initialize_database(tmp_path / "green.db", tmp_path / "b").database
    service = ConversationService(db)
    errors = []

    def work():
        try:
            service.create("from thread")
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    assert not errors
    assert [c.title for c in service.list()] == ["from thread"]
    db.close()


def test_real_upgrade_from_schema_1_to_2(tmp_path):
    """A Stage 1 database (schema 1) is backed up and upgraded; tool history gains new columns."""
    path = tmp_path / "green.db"
    v1 = initialize_database(path, tmp_path / "b", MIGRATIONS[:1])
    v1.database.execute(
        "INSERT INTO audit_log (timestamp, tool_name, category, decision, outcome, detail) "
        "VALUES ('2026-01-01T00:00:00+00:00', 'get_datetime', 'read', 'allowed', 'ok', '')"
    )
    v1.database.close()
    upgraded = initialize_database(path, tmp_path / "b")
    assert upgraded.schema_version == 2 and upgraded.backup_path.exists()
    from green.services.permission_service import AuditLog

    entry = AuditLog(upgraded.database).recent()[0]
    assert entry.tool_name == "get_datetime" and entry.summary == "" and entry.duration_ms == 0
    upgraded.database.close()
