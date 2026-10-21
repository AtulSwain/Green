"""Reusable dialogs: tool confirmation, memory editor and reminder editor."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..models import MemoryCategory, MemoryRecord, Reminder
from ..services.memory_service import MAX_MEMORY_LENGTH
from ..services.tool_registry import ConfirmationRequest


class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, document, added: str, removed: str, meta: str):
        super().__init__(document)
        self._formats = {}
        for key, colour in (("+", added), ("-", removed), ("@", meta)):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colour))
            self._formats[key] = fmt

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt API)
        if text.startswith(("+++", "---")):
            self.setFormat(0, len(text), self._formats["@"])
        elif text[:1] in self._formats:
            self.setFormat(0, len(text), self._formats[text[:1]])


class ConfirmationDialog(QDialog):
    """Shows exactly what a tool will do and requires an explicit decision."""

    def __init__(self, request: ConfirmationRequest, parent: QWidget | None = None, theme=None):
        super().__init__(parent)
        self.setWindowTitle(f"GREEN — {request.title}")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(request.title)
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        summary = QLabel(request.summary)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        layout.addWidget(self._muted(f"Tool: {request.tool_name}"))
        if request.target:
            target = QLabel(request.target)
            target.setObjectName("Mono")
            target.setWordWrap(True)
            target.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(self._muted("Target:"))
            layout.addWidget(target)

        if request.details:
            details = QPlainTextEdit(request.details)
            details.setReadOnly(True)
            details.setObjectName("DiffView" if request.details_are_diff else "CodeView")
            details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap if request.details_are_diff else QPlainTextEdit.LineWrapMode.WidgetWidth)
            details.setMinimumHeight(220 if request.details_are_diff else 120)
            if request.details_are_diff:
                added = getattr(theme, "success", "#3fa37a")
                removed = getattr(theme, "danger", "#d06060")
                meta = getattr(theme, "text_muted", "#888888")
                self._highlighter = DiffHighlighter(details.document(), added, removed, meta)
                self.resize(820, 600)
            layout.addWidget(details, 1)

        if request.destructive:
            warning = QLabel("This changes an existing file. A backup copy is kept, but please review carefully.")
            warning.setObjectName("WarningText")
            warning.setWordWrap(True)
            layout.addWidget(warning)

        buttons = QDialogButtonBox()
        self.approve_button = buttons.addButton("Approve", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = buttons.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        self.approve_button.setObjectName("PrimaryButton")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        # Safe default: Enter cancels, approval needs a deliberate click or Tab + Space.
        self.cancel_button.setDefault(True)
        layout.addWidget(buttons)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.cancel_button.setFocus()

    @staticmethod
    def _muted(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        return label


class MemoryDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, record: MemoryRecord | None = None,
                 content: str = "", category: MemoryCategory = MemoryCategory.FACT, title: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(title or ("Edit memory" if record else "Add memory"))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.content = QPlainTextEdit(record.content if record else content)
        self.content.setMinimumHeight(120)
        self.content.setTabChangesFocus(True)
        self.category = QComboBox()
        for cat in MemoryCategory:
            self.category.addItem(cat.label, cat.value)
        current = record.category if record else category
        self.category.setCurrentIndex(self.category.findData(MemoryCategory(current).value))
        self.tags = QLineEdit(", ".join(record.tags) if record else "")
        self.tags.setPlaceholderText("comma-separated, optional")
        form.addRow("Content", self.content)
        form.addRow("Category", self.category)
        form.addRow("Tags", self.tags)
        layout.addLayout(form)
        note = QLabel("Memories are stored only on this computer and are offered to the model when relevant.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        text = self.content.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Memory", "Content cannot be empty.")
            return
        if len(text) > MAX_MEMORY_LENGTH:
            QMessageBox.warning(self, "Memory", f"Content is limited to {MAX_MEMORY_LENGTH} characters.")
            return
        self.accept()

    def values(self) -> tuple[str, MemoryCategory, list[str]]:
        tags = [t.strip() for t in self.tags.text().split(",") if t.strip()]
        return self.content.toPlainText().strip(), MemoryCategory(self.category.currentData()), tags


class ReminderDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, reminder: Reminder | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit reminder" if reminder else "New reminder")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(reminder.title if reminder else "")
        self.title.setMaxLength(200)
        due = reminder.due_at.astimezone() if reminder else (datetime.now() + timedelta(hours=1)).replace(second=0, microsecond=0)
        self.due = QDateTimeEdit(QDateTime(due.year, due.month, due.day, due.hour, due.minute, 0))
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("ddd dd MMM yyyy  HH:mm")
        self.notes = QPlainTextEdit(reminder.notes if reminder else "")
        self.notes.setMaximumHeight(100)
        self.notes.setTabChangesFocus(True)
        form.addRow("Title", self.title)
        form.addRow("Due", self.due)
        form.addRow("Notes", self.notes)
        layout.addLayout(form)
        note = QLabel("Reminders appear only while GREEN is running.")
        note.setObjectName("Muted")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.title.text().strip():
            QMessageBox.warning(self, "Reminder", "Please enter a title.")
            return
        self.accept()

    def values(self) -> tuple[str, datetime, str]:
        qdt = self.due.dateTime()
        d, t = qdt.date(), qdt.time()
        due = datetime(d.year(), d.month(), d.day(), t.hour(), t.minute()).astimezone()
        return self.title.text().strip(), due, self.notes.toPlainText().strip()
