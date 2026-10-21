"""Tool permissions (deny by default) and the tool audit log."""

from __future__ import annotations

import logging

from ..config import ConfigStore
from ..database.connection import Database
from ..models import AuditEntry, from_iso, to_iso, utc_now
from ..security.secret_redaction import redact

log = logging.getLogger(__name__)

MAX_DETAIL = 300


class PermissionService:
    def __init__(self, config_store: ConfigStore):
        self.config_store = config_store

    def is_enabled(self, tool_name: str) -> bool:
        return bool(self.config_store.get().tools.enabled_tools.get(tool_name, False))

    def set_enabled(self, tool_name: str, enabled: bool) -> None:
        def mutate(config):
            config.tools.enabled_tools = {**config.tools.enabled_tools, tool_name: bool(enabled)}

        self.config_store.update(mutate)
        log.info("Tool permission changed: %s -> %s", tool_name, "enabled" if enabled else "disabled")


class AuditLog:
    """Records every tool request with its permission decision and outcome.

    Only short, redacted descriptions are stored; never file contents or full messages.
    """

    def __init__(self, db: Database):
        self.db = db

    def record(self, tool_name: str, category: str, decision: str, outcome: str, detail: str = "") -> None:
        detail = redact(" ".join(str(detail).split()))[:MAX_DETAIL]
        tool_name = redact(str(tool_name))[:80]
        try:
            self.db.execute(
                "INSERT INTO audit_log (timestamp, tool_name, category, decision, outcome, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (to_iso(utc_now()), tool_name, category, decision, outcome, detail),
            )
        except Exception:  # auditing must never break the conversation
            log.exception("Failed to write audit record")
        log.info("Tool %s: decision=%s outcome=%s", tool_name, decision, outcome)

    def recent(self, limit: int = 200) -> list[AuditEntry]:
        rows = self.db.query_all("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),))
        return [
            AuditEntry(
                r["id"], from_iso(r["timestamp"]), r["tool_name"], r["category"], r["decision"], r["outcome"], r["detail"]
            )
            for r in rows
        ]
