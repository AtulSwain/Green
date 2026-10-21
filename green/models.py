"""Plain data objects shared between services and the UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.astimezone()  # interpret naive values as local time
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def from_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass
class Conversation:
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass
class Message:
    id: int
    conversation_id: str
    role: str  # "user" | "assistant"
    content: str
    status: str  # "complete" | "cancelled" | "error"
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryCategory(StrEnum):
    PREFERENCE = "preference"
    PROJECT_NOTE = "project_note"
    FACT = "fact"
    INSTRUCTION = "instruction"
    SUMMARY = "summary"

    @property
    def label(self) -> str:
        return {
            "preference": "Preference",
            "project_note": "Project note",
            "fact": "Fact",
            "instruction": "Instruction",
            "summary": "Conversation summary",
        }[self.value]


@dataclass
class MemoryRecord:
    id: str
    content: str
    category: MemoryCategory
    tags: list[str]
    source_conversation_id: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class Reminder:
    id: str
    title: str
    notes: str
    due_at: datetime
    completed: bool
    notified_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass
class AuditEntry:
    id: int
    timestamp: datetime
    tool_name: str
    category: str
    decision: str
    outcome: str
    detail: str
