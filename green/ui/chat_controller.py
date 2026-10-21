"""Runs assistant turns in the background and bridges them to the GUI thread."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from ..services.chat_engine import ChatEngine, GenerationResult
from ..services.conversation_service import ConversationService
from ..services.ollama_service import CancelToken
from ..services.tool_registry import ConfirmationRequest
from .workers import TaskRunner

log = logging.getLogger(__name__)

TEXT_FLUSH_MS = 60


class _PendingConfirmation:
    def __init__(self, request: ConfirmationRequest):
        self.request = request
        self.event = threading.Event()
        self.approved = False


class ChatController(QObject):
    busy_changed = Signal(bool)
    user_message_added = Signal(object)  # Message
    text_updated = Signal(str, str)  # conversation_id, visible text so far
    status_changed = Signal(str)
    generation_finished = Signal(str, object)  # conversation_id, GenerationResult
    generation_failed = Signal(str, str)  # conversation_id, error message

    def __init__(
        self,
        engine: ChatEngine,
        conversations: ConversationService,
        runner: TaskRunner,
        confirm_handler: Callable[[ConfirmationRequest], bool],
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self.engine = engine
        self.conversations = conversations
        self.runner = runner
        self.confirm_handler = confirm_handler
        self._cancel: CancelToken | None = None
        self._conversation_id: str | None = None
        self._pending_text: str | None = None
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(TEXT_FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush_text)

    @property
    def is_busy(self) -> bool:
        return self._cancel is not None

    @property
    def active_conversation(self) -> str | None:
        return self._conversation_id

    def send(self, conversation_id: str, text: str) -> bool:
        """Persist the user's message and start generating. Returns False if ignored."""
        text = text.strip()
        if not text or self.is_busy:
            return False  # duplicate submissions are ignored while a turn is active
        message = self.conversations.add_message(conversation_id, "user", text)
        self.user_message_added.emit(message)
        self._start(conversation_id)
        return True

    def regenerate(self, conversation_id: str) -> bool:
        """Retry the last turn (after an error) without adding a new user message."""
        if self.is_busy:
            return False
        self._start(conversation_id)
        return True

    def _start(self, conversation_id: str) -> None:
        cancel = CancelToken()
        self._cancel = cancel
        self._conversation_id = conversation_id
        self.busy_changed.emit(True)
        self.status_changed.emit("Waiting for the model…")
        self._flush_timer.start()

        def work(progress):
            def confirm(request: ConfirmationRequest) -> bool:
                pending = _PendingConfirmation(request)
                progress(("confirm", pending))
                while not pending.event.wait(0.1):
                    if cancel.cancelled:
                        return False
                return pending.approved and not cancel.cancelled

            return self.engine.generate(
                conversation_id,
                cancel,
                on_text=lambda text: progress(("text", text)),
                on_status=lambda status: progress(("status", status)),
                confirm=confirm,
            )

        self.runner.submit(work, on_done=self._on_done, on_error=self._on_error, on_progress=self._on_progress)

    def stop(self) -> None:
        if self._cancel is not None:
            self._cancel.cancel()
            self.status_changed.emit("Stopping…")

    def _on_progress(self, payload) -> None:
        kind, value = payload
        if kind == "text":
            self._pending_text = value
        elif kind == "status":
            self.status_changed.emit(value)
        elif kind == "confirm":
            approved = False
            if self._cancel is not None and not self._cancel.cancelled:
                try:
                    approved = bool(self.confirm_handler(value.request))
                except Exception:
                    log.exception("Confirmation dialog failed")
            value.approved = approved
            value.event.set()

    def _flush_text(self) -> None:
        if self._pending_text is not None and self._conversation_id is not None:
            text, self._pending_text = self._pending_text, None
            self.text_updated.emit(self._conversation_id, text)

    def _finish(self) -> str | None:
        self._flush_text()
        self._flush_timer.stop()
        conversation_id = self._conversation_id
        self._cancel = None
        self._conversation_id = None
        self.busy_changed.emit(False)
        self.status_changed.emit("")
        return conversation_id

    def _on_done(self, result: GenerationResult) -> None:
        conversation_id = self._finish()
        self.generation_finished.emit(conversation_id or "", result)

    def _on_error(self, error: Exception) -> None:
        log.error("Unexpected generation failure: %s", error.__class__.__name__, exc_info=error)
        conversation_id = self._finish()
        self.generation_failed.emit(conversation_id or "", "Something went wrong while generating a response. Details are in the log.")
