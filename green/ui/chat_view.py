"""Conversation screen: message list, composer, streaming and voice controls."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..app.bootstrap import AppServices
from ..models import MemoryCategory, Message
from ..services.chat_engine import GenerationResult
from ..services.ollama_service import CancelToken, OllamaError
from ..services.voice_input_service import VoiceError
from .chat_controller import ChatController
from .dialogs import MemoryDialog
from .styles import repolish
from .workers import TaskRunner

log = logging.getLogger(__name__)

STATUS_LABELS = {"cancelled": "Stopped by you", "error": "Incomplete — an error occurred"}
STEP_MARKERS = {"running": "…", "waiting": "?", "ok": "✓", "failed": "✗"}
OUTCOME_LABELS = {
    "declined": "not run",
    "denied": "not permitted",
    "rejected": "invalid request",
    "cancelled": "not run",
}


def step_text_from_record(record: dict) -> str:
    """Text for a finished tool step, from a ToolResult.to_record() dict (or the Stage 1 format)."""
    description = record.get("description") or f"Tool {record.get('tool')}"
    detail = record.get("summary") or ""
    if not record.get("ok"):
        reason = OUTCOME_LABELS.get(record.get("decision", ""), "failed")
        detail = f"{reason}: {detail}" if detail else reason
    return f"{description} — {detail}" if detail else description


def step_state_from_event(event) -> str:
    if event.phase == "finished":
        return "ok" if event.result is not None and event.result.ok else "failed"
    return "waiting" if event.phase == "awaiting_approval" else "running"


def step_text_from_event(event) -> str:
    if event.phase == "finished" and event.result is not None:
        return step_text_from_record(event.result.to_record())
    if event.phase == "awaiting_approval":
        return f"{event.description} — waiting for your approval"
    return event.description


class Banner(QFrame):
    """Inline message with optional action button."""

    action_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Banner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.label, 1)
        self.action = QPushButton()
        self.action.clicked.connect(self.action_clicked)
        layout.addWidget(self.action)
        close = QPushButton("Dismiss")
        close.setObjectName("LinkButton")
        close.clicked.connect(self.hide)
        layout.addWidget(close)
        self.hide()

    def show_message(self, text: str, kind: str = "error", action: str | None = None) -> None:
        self.setProperty("kind", kind)
        repolish(self)
        self.label.setText(text)
        self.action.setVisible(bool(action))
        self.action.setText(action or "")
        self.show()


class MessageWidget(QFrame):
    remember_requested = Signal(str)

    def __init__(self, role: str, content: str, status: str = "complete", tools: list | None = None):
        super().__init__()
        self.role = role
        self.raw = content
        self.setObjectName("MessageUser" if role == "user" else "MessageAssistant")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        role_label = QLabel("You" if role == "user" else "GREEN")
        role_label.setObjectName("MessageRole")
        header.addWidget(role_label)
        header.addStretch(1)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("LinkButton")
        self.copy_button.setToolTip("Copy this message to the clipboard")
        self.copy_button.clicked.connect(self._copy)
        header.addWidget(self.copy_button)
        self.remember_button = QPushButton("Remember…")
        self.remember_button.setObjectName("LinkButton")
        self.remember_button.setToolTip("Review and save this text to long-term memory")
        self.remember_button.clicked.connect(lambda: self.remember_requested.emit(self.raw))
        header.addWidget(self.remember_button)
        layout.addLayout(header)

        self.body = QLabel()
        self.body.setObjectName("MessageBody")
        self.body.setWordWrap(True)
        # Links in model output are not clickable: GREEN never opens URLs without the browser tool.
        self.body.setOpenExternalLinks(False)
        self.body.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.body.setTextFormat(Qt.TextFormat.PlainText if role == "user" else Qt.TextFormat.MarkdownText)
        self.body.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        layout.addWidget(self.body)

        self.steps_box = QVBoxLayout()
        self.steps_box.setSpacing(2)
        layout.addLayout(self.steps_box)
        self._steps: dict[object, QLabel] = {}

        self.meta = QLabel()
        self.meta.setObjectName("MessageMeta")
        self.meta.setWordWrap(True)
        layout.addWidget(self.meta)
        self.set_content(content)
        self.set_status(status, tools or [])

    def set_content(self, text: str) -> None:
        self.raw = text
        self.body.setText(text if text else "…")
        has_text = bool(text)
        self.copy_button.setEnabled(has_text)
        self.remember_button.setEnabled(has_text)

    def update_step(self, key, text: str, state: str) -> None:
        """Show or update one tool step. ``state``: running | waiting | ok | failed."""
        label = self._steps.get(key)
        if label is None:
            label = QLabel()
            label.setObjectName("ToolStep")
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.steps_box.addWidget(label)
            self._steps[key] = label
        marker = STEP_MARKERS.get(state, "•")
        label.setText(f"{marker}  {text}")
        label.setProperty("state", state)
        repolish(label)

    def set_status(self, status: str, tools: list) -> None:
        parts = []
        if status in STATUS_LABELS:
            parts.append(STATUS_LABELS[status])
        if tools and not self._steps:
            for index, item in enumerate(tools):
                if isinstance(item, dict):
                    self.update_step(("saved", index), step_text_from_record(item), "ok" if item.get("ok") else "failed")
        self.meta.setText(" · ".join(parts))
        self.meta.setVisible(bool(parts))
        self.meta.setProperty("state", status)
        repolish(self.meta)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.raw)
        self.copy_button.setText("Copied")
        QTimer.singleShot(1200, lambda: self.copy_button.setText("Copy"))


class ComposerEdit(QPlainTextEdit):
    submit = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Composer")
        self.send_on_enter = True
        self.setPlaceholderText("Message GREEN…  (Enter to send, Shift+Enter for a new line)")
        self.setTabChangesFocus(True)
        self.setMaximumHeight(110)
        self.setMinimumHeight(64)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        is_enter = event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        modifiers = event.modifiers()
        if is_enter and self.send_on_enter and not (modifiers & Qt.KeyboardModifier.ShiftModifier):
            self.submit.emit()
            return
        if is_enter and not self.send_on_enter and modifiers & Qt.KeyboardModifier.ControlModifier:
            self.submit.emit()
            return
        super().keyPressEvent(event)


class ChatView(QWidget):
    conversation_created = Signal(str)
    conversation_changed = Signal()  # title/order may have changed

    def __init__(self, services: AppServices, controller: ChatController, runner: TaskRunner, theme_provider, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        self.controller = controller
        self.runner = runner
        self.theme_provider = theme_provider
        self.conversation_id: str | None = None
        self._load_token = 0
        self._stream_widget: MessageWidget | None = None
        self._stream_text = ""
        self._stream_steps: dict[int, object] = {}
        self._model_ready = True
        self._speaking = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(24, 16, 24, 8)
        self.title = QLabel("New conversation")
        self.title.setObjectName("PageTitle")
        header.addWidget(self.title, 1)
        self.summarize_button = QPushButton("Summarize to memory…")
        self.summarize_button.setToolTip("Ask the model for a summary, review it, then save it as a memory")
        self.summarize_button.clicked.connect(self._summarize)
        header.addWidget(self.summarize_button)
        root.addLayout(header)

        banner_box = QVBoxLayout()
        banner_box.setContentsMargins(24, 0, 24, 0)
        self.banner = Banner()
        self.banner.action_clicked.connect(self._banner_action)
        self._banner_action_kind = ""
        banner_box.addWidget(self.banner)
        root.addLayout(banner_box)

        self.stack = QStackedWidget()
        self.empty_state = self._build_empty_state()
        self.stack.addWidget(self.empty_state)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        container.setObjectName("MessageContainer")
        self.messages_layout = QVBoxLayout(container)
        self.messages_layout.setContentsMargins(24, 8, 24, 16)
        self.messages_layout.setSpacing(10)
        self.messages_layout.addStretch(1)
        self.scroll.setWidget(container)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._maybe_autoscroll)
        self._stick_to_bottom = True
        self.scroll.verticalScrollBar().valueChanged.connect(self._track_scroll)
        self.stack.addWidget(self.scroll)
        self.loading = QLabel("Loading conversation…")
        self.loading.setObjectName("Muted")
        self.loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.loading)
        root.addWidget(self.stack, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        self.status_label.setContentsMargins(24, 4, 24, 4)
        root.addWidget(self.status_label)

        composer = QFrame()
        composer.setObjectName("ComposerBar")
        row = QHBoxLayout(composer)
        row.setContentsMargins(24, 12, 24, 16)
        row.setSpacing(8)
        self.input = ComposerEdit()
        self.input.submit.connect(self.send)
        self.input.textChanged.connect(self._update_controls)
        row.addWidget(self.input, 1)
        buttons = QVBoxLayout()
        buttons.setSpacing(6)
        self.send_button = QPushButton("Send")
        self.send_button.setObjectName("PrimaryButton")
        self.send_button.setToolTip("Send message (Enter)")
        self.send_button.clicked.connect(self.send)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("DangerButton")
        self.stop_button.setToolTip("Stop generating (Esc)")
        self.stop_button.setShortcut("Esc")
        self.stop_button.clicked.connect(self.controller.stop)
        self.mic_button = QPushButton("Hold to talk")
        self.mic_button.setObjectName("MicButton")
        self.mic_button.setToolTip("Push-to-talk: hold while speaking, release to transcribe. Audio stays on this computer.")
        self.mic_button.pressed.connect(self._start_recording)
        self.mic_button.released.connect(self._stop_recording)
        self.stop_speaking_button = QPushButton("Stop speaking")
        self.stop_speaking_button.clicked.connect(self._stop_speaking)
        for button in (self.send_button, self.stop_button, self.mic_button, self.stop_speaking_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        row.addLayout(buttons)
        root.addWidget(composer)

        controller.busy_changed.connect(self._on_busy)
        controller.status_changed.connect(self.status_label.setText)
        controller.user_message_added.connect(self._on_user_message)
        controller.text_updated.connect(self._on_text)
        controller.generation_finished.connect(self._on_finished)
        controller.generation_failed.connect(self._on_failed)
        controller.tool_event.connect(self._on_tool_event)

        self.apply_config()
        self.show_conversation(None)

    # -- construction helpers ------------------------------------------------------------
    def _build_empty_state(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addStretch(1)
        title = QLabel("How can GREEN help?")
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = QLabel(
            "Ask a question, get help with code, or manage your day.\n"
            "Responses are generated by your local Ollama model; conversations are stored only on this computer.\n"
            "Enable tools under Tools & Permissions to let GREEN read approved folders or create reminders."
        )
        hint.setObjectName("Muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(2)
        return widget

    # -- configuration ----------------------------------------------------------------------
    def apply_config(self) -> None:
        config = self.services.config_store.get()
        self.input.send_on_enter = config.ui.send_on_enter
        self.input.setPlaceholderText(
            "Message GREEN…  (Enter to send, Shift+Enter for a new line)"
            if config.ui.send_on_enter
            else "Message GREEN…  (Ctrl+Enter to send)"
        )
        self.mic_button.setVisible(config.voice.enabled)
        self._update_controls()

    def set_model_ready(self, ready: bool) -> None:
        self._model_ready = ready
        self._update_controls()

    def _update_controls(self) -> None:
        busy = self.controller.is_busy
        has_text = bool(self.input.toPlainText().strip())
        self.send_button.setEnabled(not busy and has_text)
        self.stop_button.setVisible(busy)
        self.send_button.setVisible(not busy)
        self.summarize_button.setEnabled(not busy and self.conversation_id is not None)
        self.stop_speaking_button.setVisible(self._speaking)

    # -- conversations ------------------------------------------------------------------------
    def show_conversation(self, conversation_id: str | None) -> None:
        self.conversation_id = conversation_id
        self.banner.hide()
        self._load_token += 1
        token = self._load_token
        self._clear_messages()
        if conversation_id is None:
            self.title.setText("New conversation")
            self.stack.setCurrentWidget(self.empty_state)
            self._update_controls()
            return
        self.stack.setCurrentWidget(self.loading)

        def load():
            conv = self.services.conversations.get(conversation_id)
            return conv, self.services.conversations.messages(conversation_id)

        self.runner.submit(load, on_done=lambda res: self._on_loaded(token, res), on_error=lambda exc: self._on_load_error(token, exc))
        self._update_controls()

    def _on_loaded(self, token: int, result) -> None:
        if token != self._load_token:
            return  # a newer selection superseded this load
        conv, messages = result
        self.title.setText(conv.title)
        for message in messages:
            self._append_message(message)
        if self.controller.active_conversation == self.conversation_id:
            self._stream_widget = self._append_widget(MessageWidget("assistant", self._stream_text))
            for call_id, event in self._stream_steps.items():
                self._stream_widget.update_step(call_id, step_text_from_event(event), step_state_from_event(event))
        self.stack.setCurrentWidget(self.scroll if messages or self._stream_widget else self.empty_state)
        self._stick_to_bottom = True
        self._scroll_to_bottom()

    def _on_load_error(self, token: int, error: Exception) -> None:
        if token != self._load_token:
            return
        log.warning("Failed to load conversation: %s", error.__class__.__name__)
        self.stack.setCurrentWidget(self.empty_state)
        self.banner.show_message("This conversation could not be loaded.", "error")

    def refresh_title(self) -> None:
        if self.conversation_id:
            try:
                self.title.setText(self.services.conversations.get(self.conversation_id).title)
            except LookupError:
                self.show_conversation(None)

    def _clear_messages(self) -> None:
        self._stream_widget = None
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _append_widget(self, widget: MessageWidget) -> MessageWidget:
        widget.remember_requested.connect(self._remember)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, widget)
        self.stack.setCurrentWidget(self.scroll)
        return widget

    def _append_message(self, message: Message) -> MessageWidget:
        tools = message.metadata.get("tools") if isinstance(message.metadata, dict) else None
        return self._append_widget(MessageWidget(message.role, message.content, message.status, tools or []))

    # -- scrolling --------------------------------------------------------------------------------
    def _track_scroll(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        self._stick_to_bottom = value >= bar.maximum() - 40

    def _maybe_autoscroll(self, _min: int, _max: int) -> None:
        if self._stick_to_bottom:
            self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # -- sending ----------------------------------------------------------------------------------
    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text or self.controller.is_busy:
            return
        if self.conversation_id is None:
            conv = self.services.conversations.create()
            self.conversation_id = conv.id
            self._load_token += 1  # nothing to load for a brand-new conversation
            self.conversation_created.emit(conv.id)
        self.banner.hide()
        if self.controller.send(self.conversation_id, text):
            self.input.clear()

    def _on_user_message(self, message: Message) -> None:
        if message.conversation_id != self.conversation_id:
            return
        self._stick_to_bottom = True
        self._append_message(message)
        self.refresh_title()
        self.conversation_changed.emit()

    def _on_busy(self, busy: bool) -> None:
        if busy:
            self._stream_steps = {}
        if busy and self.controller.active_conversation == self.conversation_id:
            self._stream_text = ""
            self._stream_widget = self._append_widget(MessageWidget("assistant", ""))
        self._update_controls()

    def _on_text(self, conversation_id: str, text: str) -> None:
        self._stream_text = text
        if conversation_id == self.conversation_id and self._stream_widget is not None:
            self._stream_widget.set_content(text)

    def _on_tool_event(self, conversation_id: str, event) -> None:
        self._stream_steps[event.call_id] = event
        if conversation_id == self.conversation_id and self._stream_widget is not None:
            self._stream_widget.update_step(event.call_id, step_text_from_event(event), step_state_from_event(event))

    def _on_finished(self, conversation_id: str, result: GenerationResult) -> None:
        widget = self._stream_widget if conversation_id == self.conversation_id else None
        self._stream_widget = None
        self._stream_text = ""
        if widget is not None:
            if result.message is not None:
                widget.set_content(result.message.content)
                widget.set_status(result.message.status, result.tools_used)
            else:
                widget.deleteLater()
        self._stream_steps = {}
        if result.status == "error":
            self._banner_action_kind = "retry"
            if conversation_id == self.conversation_id:
                self.banner.show_message(result.error, "error", "Retry")
        elif result.notices and conversation_id == self.conversation_id:
            self._banner_action_kind = ""
            self.banner.show_message(" ".join(result.notices), "warning")
        elif result.status == "complete":
            self._maybe_speak(result.text)
        self.conversation_changed.emit()
        self._update_controls()

    def _on_failed(self, conversation_id: str, message: str) -> None:
        if self._stream_widget is not None:
            self._stream_widget.deleteLater()
            self._stream_widget = None
        self._stream_steps = {}
        self._banner_action_kind = "retry"
        self.banner.show_message(message, "error", "Retry")
        self._update_controls()

    def _banner_action(self) -> None:
        if self._banner_action_kind == "retry" and self.conversation_id:
            self.banner.hide()
            self.controller.regenerate(self.conversation_id)

    # -- memory -------------------------------------------------------------------------------------
    def _remember(self, text: str) -> None:
        dialog = MemoryDialog(self, content=text[:4000], category=MemoryCategory.FACT, title="Remember this?")
        if dialog.exec():
            content, category, tags = dialog.values()
            self.services.memory.add(content, category, tags, source_conversation_id=self.conversation_id)
            self.status_label.setText("Saved to memory.")

    def _summarize(self) -> None:
        conversation_id = self.conversation_id
        if not conversation_id:
            return
        self.summarize_button.setEnabled(False)
        self.status_label.setText("Summarizing conversation…")

        def done(summary: str) -> None:
            self.status_label.setText("")
            self._update_controls()
            if not summary:
                self.banner.show_message("The model returned an empty summary.", "warning")
                return
            dialog = MemoryDialog(self, content=summary[:4000], category=MemoryCategory.SUMMARY, title="Save conversation summary?")
            if dialog.exec():
                content, category, tags = dialog.values()
                self.services.memory.add(content, category, tags, source_conversation_id=conversation_id)
                self.status_label.setText("Summary saved to memory.")

        def failed(error: Exception) -> None:
            self.status_label.setText("")
            self._update_controls()
            message = str(error) if isinstance(error, (OllamaError, ValueError)) else "Summarizing failed."
            self.banner.show_message(message, "error")

        self.runner.submit(lambda: self.services.engine.summarize(conversation_id, CancelToken()), on_done=done, on_error=failed)

    # -- voice ----------------------------------------------------------------------------------------
    def _start_recording(self) -> None:
        try:
            self.services.voice_input.start_recording()
        except VoiceError as exc:
            self.banner.show_message(str(exc), "warning")
            return
        self._set_recording(True)
        self.mic_button.setText("Recording… release")
        self.status_label.setText("Recording from microphone (push-to-talk).")

    def _set_recording(self, recording: bool) -> None:
        self.mic_button.setProperty("recording", recording)
        repolish(self.mic_button)

    def _stop_recording(self) -> None:
        self._set_recording(False)
        self.mic_button.setText("Hold to talk")
        if not self.services.voice_input.is_recording:
            return
        try:
            audio = self.services.voice_input.stop_recording()
        except VoiceError as exc:
            self.banner.show_message(str(exc), "warning")
            return
        self.status_label.setText("Transcribing…")
        self.mic_button.setEnabled(False)

        def done(text: str) -> None:
            self.mic_button.setEnabled(True)
            self.status_label.setText("")
            current = self.input.toPlainText().strip()
            self.input.setPlainText(f"{current} {text}".strip())
            if self.services.config_store.get().voice.auto_submit_transcript:
                self.send()
            else:
                self.input.setFocus()

        def failed(error: Exception) -> None:
            self.mic_button.setEnabled(True)
            self.status_label.setText("")
            message = str(error) if isinstance(error, VoiceError) else "Transcription failed. Details are in the log."
            if not isinstance(error, VoiceError):
                log.error("Transcription failed: %s", error.__class__.__name__)
            self.banner.show_message(message, "warning")

        self.runner.submit(lambda: self.services.voice_input.transcribe(audio), on_done=done, on_error=failed)

    def _maybe_speak(self, text: str) -> None:
        voice = self.services.config_store.get().voice
        if not (voice.enabled and voice.speak_responses) or voice.muted or not text:
            return
        self._speaking = True
        self._update_controls()

        def finished(_=None) -> None:
            self._speaking = False
            self._update_controls()

        def failed(error: Exception) -> None:
            finished()
            message = str(error) if isinstance(error, VoiceError) else "Speech output failed."
            self.banner.show_message(message, "warning")

        self.runner.submit(lambda: self.services.voice_output.speak(text), on_done=finished, on_error=failed)

    def _stop_speaking(self) -> None:
        self.services.voice_output.stop()
