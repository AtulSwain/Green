"""Conversation screen: message list, composer, streaming and voice controls."""

from __future__ import annotations

import logging

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
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
from .message_body import MessageBody
from .speech import SpeechOutput
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


def format_stats(stats: dict | None) -> str:
    """'qwen3:4b · 14.2 tok/s · 6.1 s' from measured statistics (empty if none were reported)."""
    if not stats:
        return ""
    parts = [str(stats.get("model", ""))] if stats.get("model") else []
    if stats.get("tokens_per_second"):
        parts.append(f"{stats['tokens_per_second']} tok/s")
    if stats.get("seconds") is not None:
        parts.append(f"{stats['seconds']} s")
    if stats.get("load_seconds"):
        parts.append(f"model load {stats['load_seconds']} s")
    return " · ".join(parts)


class MessageWidget(QFrame):
    remember_requested = Signal(str)
    speak_requested = Signal(str)
    regenerate_requested = Signal()

    def __init__(self, role: str, content: str, status: str = "complete", tools: list | None = None, *,
                 message_id: int | None = None, created_at=None, stats: dict | None = None):
        super().__init__()
        self.role = role
        self.raw = content
        self.message_id = message_id
        self.setObjectName("MessageUser" if role == "user" else "MessageAssistant")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        role_label = QLabel("You" if role == "user" else "GREEN")
        role_label.setObjectName("MessageRole")
        header.addWidget(role_label)
        self.time_label = QLabel()
        self.time_label.setObjectName("MessageMeta")
        header.addWidget(self.time_label)
        self.set_time(created_at)
        header.addStretch(1)
        self.speak_button = self._link("Read aloud", "Read this reply aloud", lambda: self.speak_requested.emit(self.raw))
        self.copy_button = self._link("Copy", "Copy this message to the clipboard", self._copy)
        self.remember_button = self._link("Remember…", "Review and save this text to long-term memory",
                                          lambda: self.remember_requested.emit(self.raw))
        self.regenerate_button = self._link("Regenerate", "Replace this reply with a new answer",
                                            self.regenerate_requested.emit)
        for button in (self.speak_button, self.copy_button, self.remember_button, self.regenerate_button):
            header.addWidget(button)
        self.speak_button.setVisible(role == "assistant")
        self.regenerate_button.setVisible(False)
        layout.addLayout(header)

        self.body = MessageBody(markdown=role == "assistant")
        layout.addWidget(self.body)

        self.steps_box = QVBoxLayout()
        self.steps_box.setSpacing(2)
        layout.addLayout(self.steps_box)
        self._steps: dict[object, QLabel] = {}

        self.meta = QLabel()
        self.meta.setObjectName("MessageMeta")
        self.meta.setWordWrap(True)
        layout.addWidget(self.meta)
        self._status_text = ""
        self._stats_text = format_stats(stats)
        self.set_content(content)
        self.set_status(status, tools or [])

    @staticmethod
    def _link(text: str, tip: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("LinkButton")
        button.setToolTip(tip)
        button.clicked.connect(slot)
        return button

    def set_time(self, created_at) -> None:
        if created_at is None:
            self.time_label.setText("")
            return
        local = created_at.astimezone()
        self.time_label.setText(local.strftime("%H:%M"))
        self.time_label.setToolTip(local.strftime("%A %d %B %Y, %H:%M:%S"))

    def set_content(self, text: str) -> None:
        self.raw = text
        self.body.set_text(text)
        has_text = bool(text)
        for button in (self.copy_button, self.remember_button, self.speak_button):
            button.setEnabled(has_text)

    def set_stats(self, stats: dict | None) -> None:
        self._stats_text = format_stats(stats)
        self._refresh_meta()

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
        self._status_text = STATUS_LABELS.get(status, "")
        if tools and not self._steps:
            for index, item in enumerate(tools):
                if isinstance(item, dict):
                    self.update_step(("saved", index), step_text_from_record(item), "ok" if item.get("ok") else "failed")
        self.meta.setProperty("state", status)
        self._refresh_meta()

    def _refresh_meta(self) -> None:
        parts = [p for p in (self._status_text, self._stats_text) if p]
        self.meta.setText(" · ".join(parts))
        self.meta.setVisible(bool(parts))
        repolish(self.meta)

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.raw)
        self.copy_button.setText("Copied")
        QTimer.singleShot(1200, self.copy_button, lambda: self.copy_button.setText("Copy"))


QUICK_ACTIONS = (
    ("Explain code", "Paste code and get a walkthrough", "Explain what this code does, step by step:\n\n"),
    ("Write an email", "Draft a clear, friendly message", "Write a short, friendly email to [who] about [what]."),
    ("Summarize text", "Turn long text into key points", "Summarize the following text in 5 bullet points:\n\n"),
    ("Plan my day", "Organise tasks into a schedule", "Help me plan my day. My tasks are: "),
    ("Calculate", "Exact math with the calculator tool", "What is 18% of 2,450 plus 75?"),
    ("Set a reminder", "GREEN reminds you while it runs", "Remind me in 10 minutes to take a break."),
)


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
    model_switched = Signal(str)

    def __init__(self, services: AppServices, controller: ChatController, runner: TaskRunner, theme_provider,
                 speech: SpeechOutput, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        self.controller = controller
        self.runner = runner
        self.theme_provider = theme_provider
        self.speech = speech
        self.conversation_id: str | None = None
        self._load_token = 0
        self._stream_widget: MessageWidget | None = None
        self._stream_text = ""
        self._stream_steps: dict[int, object] = {}
        self._message_widgets: list[MessageWidget] = []
        self._model_ready = True
        self._voice_turn = False
        self._transcribing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(24, 16, 24, 8)
        header.setSpacing(8)
        self.title = QLabel("New conversation")
        self.title.setObjectName("PageTitle")
        header.addWidget(self.title, 1)
        self.model_combo = QComboBox()
        self.model_combo.setToolTip("Model used for new replies. Smaller models (e.g. qwen3:4b) answer faster.")
        self.model_combo.setMinimumWidth(150)
        self.model_combo.activated.connect(self._model_selected)
        header.addWidget(self.model_combo)
        self.export_button = QPushButton("Export…")
        self.export_button.setToolTip("Save this conversation as a Markdown file")
        self.export_button.clicked.connect(self._export)
        header.addWidget(self.export_button)
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

        status_row = QHBoxLayout()
        status_row.setContentsMargins(24, 4, 24, 4)
        self.status_label = QLabel("")
        self.status_label.setObjectName("Muted")
        status_row.addWidget(self.status_label, 1)
        self.level_bar = QProgressBar()
        self.level_bar.setObjectName("LevelMeter")
        self.level_bar.setRange(0, 100)
        self.level_bar.setTextVisible(False)
        self.level_bar.setFixedSize(160, 8)
        self.level_bar.setToolTip("Microphone level")
        self.level_bar.hide()
        status_row.addWidget(self.level_bar)
        root.addLayout(status_row)

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
        self.mic_button = QPushButton("Talk")
        self.mic_button.setObjectName("MicButton")
        self.mic_button.setToolTip(
            "Click and speak. Recording stops when you pause (or click again); GREEN then answers out loud. "
            "Audio never leaves this computer. Shortcut: Ctrl+M"
        )
        self.mic_button.setShortcut("Ctrl+M")
        self.mic_button.clicked.connect(self.toggle_recording)
        self.stop_speaking_button = QPushButton("Stop speaking")
        self.stop_speaking_button.clicked.connect(self.speech.stop)
        for button in (self.send_button, self.stop_button, self.mic_button, self.stop_speaking_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        row.addLayout(buttons)
        root.addWidget(composer)

        self._voice_timer = QTimer(self)
        self._voice_timer.setInterval(100)
        self._voice_timer.timeout.connect(self._poll_recording)

        controller.busy_changed.connect(self._on_busy)
        controller.status_changed.connect(self.status_label.setText)
        controller.user_message_added.connect(self._on_user_message)
        controller.text_updated.connect(self._on_text)
        controller.generation_finished.connect(self._on_finished)
        controller.generation_failed.connect(self._on_failed)
        controller.tool_event.connect(self._on_tool_event)
        speech.speaking_changed.connect(lambda _: self._update_controls())
        speech.failed.connect(lambda message: self.banner.show_message(message, "warning"))

        self.apply_config()
        self.show_conversation(None)

    # -- construction helpers ------------------------------------------------------------
    def _build_empty_state(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.addStretch(1)
        title = QLabel("How can GREEN help?")
        title.setObjectName("PageTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = QLabel("Runs on your computer with your local model. Pick a starting point or just type.")
        hint.setObjectName("Muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(16)
        grid = QGridLayout()
        grid.setSpacing(10)
        self.quick_buttons: list[QPushButton] = []
        for index, (name, subtitle, prompt) in enumerate(QUICK_ACTIONS):
            button = QPushButton(f"{name}\n{subtitle}")
            button.setObjectName("QuickAction")
            button.setToolTip(prompt.strip())
            button.setMinimumHeight(64)
            button.clicked.connect(lambda _=False, p=prompt: self.use_prompt(p))
            grid.addWidget(button, index // 3, index % 3)
            self.quick_buttons.append(button)
        wrapper = QHBoxLayout()
        wrapper.addStretch(1)
        wrapper.addLayout(grid, 6)
        wrapper.addStretch(1)
        layout.addLayout(wrapper)
        layout.addStretch(2)
        return widget

    def use_prompt(self, prompt: str) -> None:
        self.input.setPlainText(prompt)
        cursor = self.input.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.input.setTextCursor(cursor)
        self.input.setFocus()

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
        if self.model_combo.findText(config.ollama.model) < 0:
            self.model_combo.addItem(config.ollama.model)
        self.model_combo.setCurrentText(config.ollama.model)
        self._update_controls()

    def set_models(self, names: list[str], current: str) -> None:
        """Fill the model switcher with the models installed in Ollama."""
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for name in sorted(set(names) | {current}):
            self.model_combo.addItem(name)
        self.model_combo.setCurrentText(current)
        self.model_combo.blockSignals(False)

    def _model_selected(self, _index: int) -> None:
        name = self.model_combo.currentText()
        if not name or name == self.services.config_store.get().ollama.model:
            return
        try:
            self.services.config_store.update(lambda c: setattr(c.ollama, "model", name))
        except Exception as exc:  # validation or disk error
            self.banner.show_message(f"Could not switch model: {exc}", "error")
            return
        self.status_label.setText(f"Now using {name} for new replies.")
        self.model_switched.emit(name)

    def set_model_ready(self, ready: bool) -> None:
        self._model_ready = ready
        self._update_controls()

    def _update_controls(self) -> None:
        busy = self.controller.is_busy
        recording = self.services.voice_input.is_recording
        has_text = bool(self.input.toPlainText().strip())
        self.send_button.setEnabled(not busy and has_text and not recording)
        self.stop_button.setVisible(busy)
        self.send_button.setVisible(not busy)
        has_conversation = self.conversation_id is not None
        self.summarize_button.setEnabled(not busy and has_conversation)
        self.export_button.setEnabled(has_conversation and bool(self._message_widgets))
        self.model_combo.setEnabled(not busy)
        self.mic_button.setEnabled(not busy and not self._transcribing)
        self.stop_speaking_button.setVisible(self.speech.is_speaking)
        self._refresh_regenerate()

    def _refresh_regenerate(self) -> None:
        busy = self.controller.is_busy
        for index, widget in enumerate(self._message_widgets):
            is_last = index == len(self._message_widgets) - 1
            widget.regenerate_button.setVisible(is_last and widget.role == "assistant" and not busy)

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
        self._update_controls()

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
        self._message_widgets = []
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _append_widget(self, widget: MessageWidget) -> MessageWidget:
        widget.remember_requested.connect(self._remember)
        widget.speak_requested.connect(lambda text: self.speech.speak(text, respect_mute=False))
        widget.regenerate_requested.connect(self.regenerate_last)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, widget)
        self.stack.setCurrentWidget(self.scroll)
        return widget

    def _append_message(self, message: Message) -> MessageWidget:
        metadata = message.metadata if isinstance(message.metadata, dict) else {}
        widget = self._append_widget(MessageWidget(
            message.role, message.content, message.status, metadata.get("tools") or [],
            message_id=message.id, created_at=message.created_at, stats=metadata.get("stats"),
        ))
        self._message_widgets.append(widget)
        return widget

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
        self.speech.stop()
        if self.controller.send(self.conversation_id, text):
            self.input.clear()

    def regenerate_last(self) -> None:
        """Replace the last reply with a new one."""
        conversation_id = self.conversation_id
        if not conversation_id or self.controller.is_busy:
            return
        last_user = self.services.conversations.last_user_message(conversation_id)
        if last_user is None:
            return
        self.services.conversations.delete_messages_after(conversation_id, last_user.id)
        while self._message_widgets and self._message_widgets[-1].role == "assistant":
            widget = self._message_widgets.pop()
            self.messages_layout.removeWidget(widget)
            widget.deleteLater()
        self.banner.hide()
        self.controller.regenerate(conversation_id)

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
                widget.message_id = result.message.id
                widget.set_content(result.message.content)
                widget.set_status(result.message.status, result.tools_used)
                widget.set_time(result.message.created_at)
                widget.set_stats(result.stats)
                self._message_widgets.append(widget)
            else:
                self.messages_layout.removeWidget(widget)
                widget.deleteLater()
        self._stream_steps = {}
        voice_turn, self._voice_turn = self._voice_turn, False
        if result.status == "error":
            self._banner_action_kind = "retry"
            if conversation_id == self.conversation_id:
                self.banner.show_message(result.error, "error", "Retry")
        else:
            if result.notices and conversation_id == self.conversation_id:
                self._banner_action_kind = ""
                self.banner.show_message(" ".join(result.notices), "warning")
            if result.status == "complete":
                self._maybe_speak(result.text, voice_turn)
        self.conversation_changed.emit()
        self._update_controls()

    def _on_failed(self, conversation_id: str, message: str) -> None:
        if self._stream_widget is not None:
            self.messages_layout.removeWidget(self._stream_widget)
            self._stream_widget.deleteLater()
            self._stream_widget = None
        self._stream_steps = {}
        self._voice_turn = False
        self._banner_action_kind = "retry"
        self.banner.show_message(message, "error", "Retry")
        self._update_controls()

    def _banner_action(self) -> None:
        if self._banner_action_kind == "retry" and self.conversation_id:
            self.banner.hide()
            self.controller.regenerate(self.conversation_id)

    # -- export -------------------------------------------------------------------------------------
    def _export(self) -> None:
        if not self.conversation_id:
            return
        title = self.title.text().strip() or "conversation"
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in title)[:60].strip() or "conversation"
        path, _ = QFileDialog.getSaveFileName(self, "Export conversation", f"{safe}.md", "Markdown (*.md);;Text (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self.services.conversations.export_markdown(self.conversation_id), encoding="utf-8")
        except OSError as exc:
            self.banner.show_message(f"Could not save the file: {exc.strerror or exc}", "error")
            return
        self.status_label.setText(f"Exported to {path}")

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
    def toggle_recording(self) -> None:
        if self.services.voice_input.is_recording:
            self._finish_recording()
            return
        if self.controller.is_busy or self._transcribing:
            return
        self.speech.stop()  # do not record GREEN's own voice
        try:
            self.services.voice_input.start_recording()
        except VoiceError as exc:
            self.banner.show_message(f"{exc} Open the Voice screen to set up voice.", "warning")
            return
        self._set_recording(True)
        self._voice_timer.start()

    def _set_recording(self, recording: bool) -> None:
        self.mic_button.setProperty("recording", recording)
        self.mic_button.setText("Listening… click to stop" if recording else "Talk")
        repolish(self.mic_button)
        self.level_bar.setVisible(recording)
        self.level_bar.setValue(0)
        if recording:
            auto = self.services.config_store.get().voice.auto_stop_on_silence
            self.status_label.setText("Listening… " + ("stops when you pause." if auto else "click Talk again when done."))
        self._update_controls()

    def _poll_recording(self) -> None:
        voice_input = self.services.voice_input
        if not voice_input.is_recording:
            self._voice_timer.stop()
            return
        self.level_bar.setValue(int(voice_input.level * 100))
        if voice_input.should_auto_stop():
            self._finish_recording()

    def _finish_recording(self) -> None:
        self._voice_timer.stop()
        self._set_recording(False)
        if not self.services.voice_input.is_recording:
            return
        try:
            audio = self.services.voice_input.stop_recording()
        except VoiceError as exc:
            self.banner.show_message(str(exc), "warning")
            return
        self._transcribing = True
        self.status_label.setText("Transcribing…")
        self._update_controls()

        def done(text: str) -> None:
            self._transcribing = False
            self.status_label.setText("")
            voice = self.services.config_store.get().voice
            current = self.input.toPlainText().strip()
            self.input.setPlainText(f"{current} {text}".strip())
            if voice.conversation_mode or voice.auto_submit_transcript:
                self._voice_turn = voice.conversation_mode
                self.send()
            else:
                self.input.setFocus()
            self._update_controls()

        def failed(error: Exception) -> None:
            self._transcribing = False
            self.status_label.setText("")
            message = str(error) if isinstance(error, VoiceError) else "Transcription failed. Details are in the log."
            if not isinstance(error, VoiceError):
                log.error("Transcription failed: %s", error.__class__.__name__)
            self.banner.show_message(message, "warning")
            self._update_controls()

        self.runner.submit(lambda: self.services.voice_input.transcribe(audio), on_done=done, on_error=failed)

    def _maybe_speak(self, text: str, voice_turn: bool) -> None:
        voice = self.services.config_store.get().voice
        should = (voice_turn and voice.conversation_mode) or (voice.enabled and voice.speak_responses)
        if should and text:
            self.speech.speak(text)
            self._update_controls()

    def shutdown(self) -> None:
        self._voice_timer.stop()
        self.speech.stop()
