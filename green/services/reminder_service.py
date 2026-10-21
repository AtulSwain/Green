"""Local reminders.

Reminders are only shown while GREEN is running. Nothing is registered with the
Windows Task Scheduler.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from ..database.connection import Database
from ..models import Reminder, from_iso, to_iso, utc_now

MAX_TITLE = 200
MAX_NOTES = 2000


class ReminderNotFoundError(LookupError):
    pass


def _reminder(row) -> Reminder:
    return Reminder(
        id=row["id"],
        title=row["title"],
        notes=row["notes"],
        due_at=from_iso(row["due_at"]),
        completed=bool(row["completed"]),
        notified_at=from_iso(row["notified_at"]) if row["notified_at"] else None,
        created_at=from_iso(row["created_at"]),
        updated_at=from_iso(row["updated_at"]),
    )


def _clean(title: str, notes: str) -> tuple[str, str]:
    title = " ".join((title or "").split())
    if not title:
        raise ValueError("Reminder title cannot be empty")
    if len(title) > MAX_TITLE:
        raise ValueError(f"Reminder title is limited to {MAX_TITLE} characters")
    notes = (notes or "").strip()
    if len(notes) > MAX_NOTES:
        raise ValueError(f"Reminder notes are limited to {MAX_NOTES} characters")
    return title, notes


class ReminderService:
    def __init__(self, db: Database):
        self.db = db

    def create(self, title: str, due_at: datetime, notes: str = "") -> Reminder:
        title, notes = _clean(title, notes)
        now = to_iso(utc_now())
        reminder_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO reminders (id, title, notes, due_at, completed, notified_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0, NULL, ?, ?)",
            (reminder_id, title, notes, to_iso(due_at), now, now),
        )
        return self.get(reminder_id)

    def get(self, reminder_id: str) -> Reminder:
        row = self.db.query_one("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        if row is None:
            raise ReminderNotFoundError(reminder_id)
        return _reminder(row)

    def update(self, reminder_id: str, title: str, due_at: datetime, notes: str = "") -> Reminder:
        title, notes = _clean(title, notes)
        current = self.get(reminder_id)
        # Rescheduling re-arms the notification.
        reset = current.due_at != from_iso(to_iso(due_at))
        cur = self.db.execute(
            "UPDATE reminders SET title = ?, notes = ?, due_at = ?, updated_at = ?"
            + (", notified_at = NULL" if reset else "")
            + " WHERE id = ?",
            (title, notes, to_iso(due_at), to_iso(utc_now()), reminder_id),
        )
        if cur.rowcount == 0:
            raise ReminderNotFoundError(reminder_id)
        return self.get(reminder_id)

    def set_completed(self, reminder_id: str, completed: bool = True) -> Reminder:
        cur = self.db.execute(
            "UPDATE reminders SET completed = ?, updated_at = ? WHERE id = ?",
            (1 if completed else 0, to_iso(utc_now()), reminder_id),
        )
        if cur.rowcount == 0:
            raise ReminderNotFoundError(reminder_id)
        return self.get(reminder_id)

    def delete(self, reminder_id: str) -> None:
        cur = self.db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        if cur.rowcount == 0:
            raise ReminderNotFoundError(reminder_id)

    def list(self, include_completed: bool = True) -> list[Reminder]:
        where = "" if include_completed else "WHERE completed = 0"
        rows = self.db.query_all(f"SELECT * FROM reminders {where} ORDER BY completed, due_at")
        return [_reminder(r) for r in rows]

    def due(self, now: datetime | None = None) -> list[Reminder]:
        """Incomplete reminders that are due and have not been shown yet."""
        now_iso = to_iso(now or utc_now())
        rows = self.db.query_all(
            "SELECT * FROM reminders WHERE completed = 0 AND notified_at IS NULL AND due_at <= ? ORDER BY due_at",
            (now_iso,),
        )
        return [_reminder(r) for r in rows]

    def mark_notified(self, reminder_id: str) -> None:
        self.db.execute(
            "UPDATE reminders SET notified_at = ? WHERE id = ?", (to_iso(utc_now()), reminder_id)
        )
