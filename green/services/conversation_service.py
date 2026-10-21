"""Persistence of conversations and their messages."""

from __future__ import annotations

import json
import uuid
from typing import Any

from ..database.connection import Database
from ..models import Conversation, Message, from_iso, to_iso, utc_now

DEFAULT_TITLE = "New conversation"
MAX_TITLE_LENGTH = 120


class ConversationNotFoundError(LookupError):
    pass


def _conversation(row) -> Conversation:
    return Conversation(row["id"], row["title"], from_iso(row["created_at"]), from_iso(row["updated_at"]))


def _message(row) -> Message:
    try:
        metadata = json.loads(row["metadata"] or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return Message(
        id=row["id"],
        conversation_id=row["conversation_id"],
        role=row["role"],
        content=row["content"],
        status=row["status"],
        created_at=from_iso(row["created_at"]),
        metadata=metadata,
    )


def title_from_text(text: str) -> str:
    line = " ".join(text.split())
    if not line:
        return DEFAULT_TITLE
    return line if len(line) <= 48 else line[:47].rstrip() + "…"


class ConversationService:
    def __init__(self, db: Database):
        self.db = db

    def create(self, title: str = DEFAULT_TITLE) -> Conversation:
        now = to_iso(utc_now())
        conv_id = uuid.uuid4().hex
        title = self._clean_title(title)
        self.db.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conv_id, title, now, now),
        )
        return self.get(conv_id)

    def get(self, conversation_id: str) -> Conversation:
        row = self.db.query_one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))
        if row is None:
            raise ConversationNotFoundError(conversation_id)
        return _conversation(row)

    def exists(self, conversation_id: str) -> bool:
        return self.db.query_one("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)) is not None

    def list(self) -> list[Conversation]:
        rows = self.db.query_all("SELECT * FROM conversations ORDER BY updated_at DESC, created_at DESC")
        return [_conversation(r) for r in rows]

    @staticmethod
    def _clean_title(title: str) -> str:
        title = " ".join((title or "").split())
        if not title:
            raise ValueError("Title cannot be empty")
        return title[:MAX_TITLE_LENGTH]

    def rename(self, conversation_id: str, title: str) -> Conversation:
        title = self._clean_title(title)
        cur = self.db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, to_iso(utc_now()), conversation_id),
        )
        if cur.rowcount == 0:
            raise ConversationNotFoundError(conversation_id)
        return self.get(conversation_id)

    def delete(self, conversation_id: str) -> None:
        cur = self.db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        if cur.rowcount == 0:
            raise ConversationNotFoundError(conversation_id)

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        status: str = "complete",
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        if role not in ("user", "assistant"):
            raise ValueError(f"Unsupported role: {role}")
        now = to_iso(utc_now())
        with self.db.transaction() as conn:
            row = conn.execute("SELECT title FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if row is None:
                raise ConversationNotFoundError(conversation_id)
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, content, status, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (conversation_id, role, content, status, json.dumps(metadata or {}), now),
            )
            message_id = cur.lastrowid
            title = row["title"]
            if role == "user" and title == DEFAULT_TITLE:
                title = title_from_text(content)
            conn.execute(
                "UPDATE conversations SET updated_at = ?, title = ? WHERE id = ?",
                (now, title, conversation_id),
            )
        return self.get_message(message_id)

    def get_message(self, message_id: int) -> Message:
        row = self.db.query_one("SELECT * FROM messages WHERE id = ?", (message_id,))
        if row is None:
            raise LookupError(message_id)
        return _message(row)

    def messages(self, conversation_id: str) -> list[Message]:
        rows = self.db.query_all(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)
        )
        return [_message(r) for r in rows]

    def recent_messages(self, conversation_id: str, limit: int) -> list[Message]:
        rows = self.db.query_all(
            "SELECT * FROM (SELECT * FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?) "
            "ORDER BY id",
            (conversation_id, int(limit)),
        )
        return [_message(r) for r in rows]
