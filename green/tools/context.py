"""Dependencies shared by tool implementations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig
from ..security.path_policy import PathPolicy
from ..services.memory_service import MemoryService
from ..services.reminder_service import ReminderService


@dataclass
class ToolContext:
    config: Callable[[], AppConfig]
    memory: MemoryService
    reminders: ReminderService
    file_backup_dir: Path
    current_conversation: Callable[[], str | None] = lambda: None

    def approved_policy(self) -> PathPolicy:
        return PathPolicy(self.config().tools.approved_directories)

    def output_policy(self) -> PathPolicy:
        return PathPolicy([self.config().tools.output_directory])

    def workspace_policy(self) -> PathPolicy:
        return PathPolicy([self.config().workspace.project_directory])
