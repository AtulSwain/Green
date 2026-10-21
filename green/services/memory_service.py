"""User-approved long-term memory.

Nothing is stored here unless the user adds it directly or approves a proposal.
Retrieval returns only records relevant to the current request instead of the
entire memory database.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable

from ..database.connection import Database
from ..models import MemoryCategory, MemoryRecord, from_iso, to_iso, utc_now

MAX_MEMORY_LENGTH = 4000
MAX_TAGS = 12

_WORD_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_\-+.#]*")
_STOPWORDS = frozenset(
    """a an and are as at be but by can could did do does for from get got had has have he her
    his how i if in into is it its just like me my no not of on or our please she so that the
    their them then there these they this to us was we were what when where which who why will
    with would you your about tell know want need make give show also some any all""".split()
)


class MemoryNotFoundError(LookupError):
    pass


def _record(row) -> MemoryRecord:
    try:
        tags = json.loads(row["tags"] or "[]")
    except json.JSONDecodeError:
        tags = []
    return MemoryRecord(
        id=row["id"],
        content=row["content"],
        category=MemoryCategory(row["category"]),
        tags=[str(t) for t in tags],
        source_conversation_id=row["source_conversation_id"],
        created_at=from_iso(row["created_at"]),
        updated_at=from_iso(row["updated_at"]),
    )


def clean_tags(tags: Iterable[str] | None) -> list[str]:
    cleaned: list[str] = []
    for tag in tags or []:
        tag = " ".join(str(tag).split()).lower()[:32]
        if tag and tag not in cleaned:
            cleaned.append(tag)
    return cleaned[:MAX_TAGS]


def keywords(text: str, limit: int = 12) -> list[str]:
    found: list[str] = []
    for word in _WORD_RE.findall(text.lower()):
        word = word.strip(".-")
        if len(word) < 3 or word in _STOPWORDS or word in found:
            continue
        found.append(word)
        if len(found) >= limit:
            break
    return found


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class MemoryService:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _validate(content: str, category: MemoryCategory | str) -> tuple[str, MemoryCategory]:
        content = (content or "").strip()
        if not content:
            raise ValueError("Memory content cannot be empty")
        if len(content) > MAX_MEMORY_LENGTH:
            raise ValueError(f"Memory content is limited to {MAX_MEMORY_LENGTH} characters")
        return content, MemoryCategory(category)

    def add(
        self,
        content: str,
        category: MemoryCategory | str,
        tags: Iterable[str] | None = None,
        source_conversation_id: str | None = None,
    ) -> MemoryRecord:
        content, category = self._validate(content, category)
        now = to_iso(utc_now())
        memory_id = uuid.uuid4().hex
        if source_conversation_id is not None and not self.db.query_one(
            "SELECT 1 FROM conversations WHERE id = ?", (source_conversation_id,)
        ):
            source_conversation_id = None
        self.db.execute(
            "INSERT INTO memories (id, content, category, tags, source_conversation_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (memory_id, content, category.value, json.dumps(clean_tags(tags)), source_conversation_id, now, now),
        )
        return self.get(memory_id)

    def get(self, memory_id: str) -> MemoryRecord:
        row = self.db.query_one("SELECT * FROM memories WHERE id = ?", (memory_id,))
        if row is None:
            raise MemoryNotFoundError(memory_id)
        return _record(row)

    def update(
        self,
        memory_id: str,
        content: str,
        category: MemoryCategory | str,
        tags: Iterable[str] | None = None,
    ) -> MemoryRecord:
        content, category = self._validate(content, category)
        cur = self.db.execute(
            "UPDATE memories SET content = ?, category = ?, tags = ?, updated_at = ? WHERE id = ?",
            (content, category.value, json.dumps(clean_tags(tags)), to_iso(utc_now()), memory_id),
        )
        if cur.rowcount == 0:
            raise MemoryNotFoundError(memory_id)
        return self.get(memory_id)

    def delete(self, memory_id: str) -> None:
        cur = self.db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        if cur.rowcount == 0:
            raise MemoryNotFoundError(memory_id)

    def clear_all(self) -> int:
        with self.db.transaction() as conn:
            return conn.execute("DELETE FROM memories").rowcount

    def count(self) -> int:
        return int(self.db.query_one("SELECT COUNT(*) FROM memories")[0])

    def search(self, query: str = "", category: MemoryCategory | str | None = None, limit: int = 500) -> list[MemoryRecord]:
        clauses: list[str] = []
        params: list[object] = []
        query = (query or "").strip()
        if query:
            like = f"%{_escape_like(query.lower())}%"
            clauses.append("(lower(content) LIKE ? ESCAPE '\\' OR lower(tags) LIKE ? ESCAPE '\\')")
            params.extend([like, like])
        if category:
            clauses.append("category = ?")
            params.append(MemoryCategory(category).value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query_all(
            f"SELECT * FROM memories {where} ORDER BY updated_at DESC LIMIT ?", (*params, int(limit))
        )
        return [_record(r) for r in rows]

    def relevant(self, text: str, limit: int) -> list[MemoryRecord]:
        """Return up to ``limit`` memories that share keywords with ``text``."""
        if limit <= 0:
            return []
        words = keywords(text)
        if not words:
            return []
        clause = " OR ".join(["lower(content) LIKE ? ESCAPE '\\' OR lower(tags) LIKE ? ESCAPE '\\'"] * len(words))
        params: list[object] = []
        for word in words:
            like = f"%{_escape_like(word)}%"
            params.extend([like, like])
        rows = self.db.query_all(f"SELECT * FROM memories WHERE {clause} LIMIT 500", params)
        scored: list[tuple[float, MemoryRecord]] = []
        for record in (_record(r) for r in rows):
            haystack = (record.content + " " + " ".join(record.tags)).lower()
            hits = sum(1 for word in words if word in haystack)
            if hits == 0:
                continue
            bonus = 0.5 if record.category in (MemoryCategory.INSTRUCTION, MemoryCategory.PREFERENCE) else 0.0
            scored.append((hits + bonus, record))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return [record for _, record in scored[:limit]]
