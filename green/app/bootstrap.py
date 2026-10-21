"""Construction of the service layer, independent of the GUI."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import ConfigStore
from ..database.connection import Database
from ..database.migrations import initialize_database
from ..logging_setup import RecentErrorsHandler, configure_logging
from ..paths import AppPaths, default_paths
from ..services.chat_engine import ChatEngine
from ..services.conversation_service import ConversationService
from ..services.memory_service import MemoryService
from ..services.ollama_service import OllamaService
from ..services.permission_service import AuditLog, PermissionService
from ..services.reminder_service import ReminderService
from ..services.tool_registry import ToolRegistry
from ..services.voice_input_service import VoiceInputService
from ..services.voice_output_service import VoiceOutputService
from ..tools import ToolContext, register_default_tools

log = logging.getLogger(__name__)


@dataclass
class AppServices:
    paths: AppPaths
    config_store: ConfigStore
    database: Database
    conversations: ConversationService
    memory: MemoryService
    reminders: ReminderService
    permissions: PermissionService
    audit: AuditLog
    registry: ToolRegistry
    tool_context: ToolContext
    ollama: OllamaService
    engine: ChatEngine
    voice_input: VoiceInputService
    voice_output: VoiceOutputService
    recent_errors: RecentErrorsHandler | None = None
    startup_notices: list[str] = field(default_factory=list)

    def shutdown(self) -> None:
        try:
            self.voice_output.stop()
        finally:
            self.database.close()


def build_services(paths: AppPaths | None = None, *, configure_logs: bool = True) -> AppServices:
    """Create all services. Raises DatabaseInitError if the database cannot be used."""
    paths = paths or default_paths()
    paths.ensure()
    recent = configure_logging(paths.log_dir) if configure_logs else None

    store = ConfigStore(paths.config_file)
    store.load()
    notices = list(store.warnings)
    if not paths.config_file.exists():
        store.save(store.get())

    init = initialize_database(paths.database_file, paths.backup_dir)
    notices.extend(init.notices)
    db = init.database

    conversations = ConversationService(db)
    memory = MemoryService(db)
    reminders = ReminderService(db)
    permissions = PermissionService(store)
    audit = AuditLog(db)
    registry = ToolRegistry(permissions, audit)
    context = ToolContext(
        config=store.get, memory=memory, reminders=reminders, file_backup_dir=paths.file_backup_dir
    )
    ollama = OllamaService(lambda: store.get().ollama)
    context.ollama = ollama
    register_default_tools(registry, context)
    engine = ChatEngine(ollama, conversations, memory, registry, store.get)
    voice_in = VoiceInputService(lambda: store.get().voice)
    voice_out = VoiceOutputService(lambda: store.get().voice)
    log.info("Services initialised (schema %d)", init.schema_version)
    return AppServices(
        paths, store, db, conversations, memory, reminders, permissions, audit, registry, context,
        ollama, engine, voice_in, voice_out, recent, notices,
    )
