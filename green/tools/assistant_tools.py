"""Tools that store user data in GREEN itself (memories, reminders). Always confirmed."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from ..models import MemoryCategory
from ..services.tool_registry import ConfirmationRequest, Tool, ToolArgs, ToolCategory, ToolError
from .context import ToolContext


class SaveMemoryArgs(ToolArgs):
    content: str = Field(..., min_length=1, max_length=4000)
    category: MemoryCategory = MemoryCategory.FACT
    tags: list[str] = Field(default_factory=list, max_length=12)


class SaveMemoryTool(Tool):
    name = "save_memory"
    description = (
        "Propose saving information to long-term memory. Use only when the user asks you to remember "
        "something. The user must approve it before anything is saved."
    )
    category = ToolCategory.CONTROLLED
    Args = SaveMemoryArgs
    requires_confirmation = True

    def __init__(self, context: ToolContext):
        self.context = context

    def confirmation(self, args, prepared):
        return ConfirmationRequest(
            self.name,
            "Save to memory?",
            f"GREEN wants to remember this ({MemoryCategory(args.category).label.lower()}):",
            details=args.content,
        )

    def describe(self, args, prepared):
        return f"Saving a memory ({MemoryCategory(args.category).label.lower()})"

    def summarize(self, data):
        return "Memory saved"

    def audit_detail(self, args, prepared):
        return f"category={args.category}"

    def run(self, args, prepared):
        record = self.context.memory.add(
            args.content, args.category, args.tags, source_conversation_id=self.context.current_conversation()
        )
        return {"saved_memory_id": record.id}


class CreateReminderArgs(ToolArgs):
    title: str = Field(..., min_length=1, max_length=200)
    due: str = Field(..., description="Local date and time in ISO 8601 format, e.g. 2026-05-01T09:30")
    notes: str = Field("", max_length=2000)


class CreateReminderTool(Tool):
    name = "create_reminder"
    description = (
        "Create a reminder shown by GREEN at the given local time while GREEN is running. "
        "Call get_datetime first if you need the current date."
    )
    category = ToolCategory.CONTROLLED
    Args = CreateReminderArgs
    requires_confirmation = True

    def __init__(self, context: ToolContext):
        self.context = context

    def prepare(self, args):
        try:
            due = datetime.fromisoformat(args.due)
        except ValueError as exc:
            raise ToolError("The due time must be an ISO 8601 date and time such as 2026-05-01T09:30.") from exc
        if due.tzinfo is None:
            due = due.astimezone()  # local time
        return due

    def confirmation(self, args, prepared):
        when = prepared.astimezone().strftime("%a %d %b %Y, %H:%M")
        return ConfirmationRequest(self.name, "Create reminder?", f"“{args.title}” at {when}", details=args.notes)

    def describe(self, args, prepared):
        return f"Creating a reminder for {prepared.astimezone():%a %d %b %H:%M}"

    def summarize(self, data):
        return f"Reminder created for {data['due']}"

    def audit_detail(self, args, prepared):
        return f"due={prepared.isoformat(timespec='minutes')}"

    def run(self, args, prepared):
        reminder = self.context.reminders.create(args.title, prepared, args.notes)
        return {"reminder_id": reminder.id, "due": prepared.isoformat(timespec="minutes")}
