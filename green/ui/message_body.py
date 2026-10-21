"""Rendering of message text: Markdown paragraphs plus real code blocks with a Copy button."""

from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

_FENCE = re.compile(r"^\s{0,3}(```|~~~)\s*([\w+#.\-]*)\s*$")


@dataclass
class Segment:
    kind: str  # "text" | "code"
    content: str
    language: str = ""


def parse_segments(text: str) -> list[Segment]:
    """Split Markdown into text and fenced code segments. An unclosed fence (while streaming) is code."""
    segments: list[Segment] = []
    buffer: list[str] = []
    in_code = False
    fence = ""
    language = ""
    for line in text.split("\n"):
        match = _FENCE.match(line)
        if not in_code and match:
            if any(part.strip() for part in buffer):
                segments.append(Segment("text", "\n".join(buffer).strip("\n")))
            buffer, in_code, fence, language = [], True, match.group(1), match.group(2)
        elif in_code and line.strip() == fence:
            segments.append(Segment("code", "\n".join(buffer), language))
            buffer, in_code = [], False
        else:
            buffer.append(line)
    if in_code:
        segments.append(Segment("code", "\n".join(buffer), language))
    elif any(part.strip() for part in buffer):
        segments.append(Segment("text", "\n".join(buffer).strip("\n")))
    return segments


def _text_label(markdown: bool) -> QLabel:
    label = QLabel()
    label.setObjectName("MessageBody")
    label.setWordWrap(True)
    # Links in model output are not clickable: GREEN never opens URLs without the browser tool.
    label.setOpenExternalLinks(False)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
    label.setTextFormat(Qt.TextFormat.MarkdownText if markdown else Qt.TextFormat.PlainText)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
    return label


class CodeBlock(QFrame):
    def __init__(self, code: str, language: str):
        super().__init__()
        self.setObjectName("CodeBlock")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 8)
        layout.setSpacing(4)
        header = QHBoxLayout()
        self.language = QLabel(language or "code")
        self.language.setObjectName("CodeLanguage")
        header.addWidget(self.language)
        header.addStretch(1)
        self.copy_button = QPushButton("Copy code")
        self.copy_button.setObjectName("LinkButton")
        self.copy_button.clicked.connect(self._copy)
        header.addWidget(self.copy_button)
        layout.addLayout(header)
        self.code = QLabel()
        self.code.setObjectName("CodeText")
        self.code.setTextFormat(Qt.TextFormat.PlainText)
        self.code.setWordWrap(True)
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        layout.addWidget(self.code)
        self.set_code(code, language)

    def set_code(self, code: str, language: str) -> None:
        self.raw = code
        self.code.setText(code or " ")
        self.language.setText(language or "code")

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.raw)
        self.copy_button.setText("Copied")
        QTimer.singleShot(1200, self.copy_button, lambda: self.copy_button.setText("Copy code"))


class MessageBody(QWidget):
    """Shows message text. Updates in place while streaming to avoid flicker."""

    def __init__(self, markdown: bool):
        super().__init__()
        self.markdown = markdown
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._widgets: list[QWidget] = []
        self._kinds: list[str] = []

    def set_text(self, text: str) -> None:
        segments = parse_segments(text) if self.markdown else [Segment("text", text)]
        if not segments:
            segments = [Segment("text", "…")]
        kinds = [s.kind for s in segments]
        if kinds[: len(self._kinds)] != self._kinds:
            self._clear()
        for index, segment in enumerate(segments):
            if index < len(self._widgets):
                widget = self._widgets[index]
            else:
                widget = CodeBlock("", "") if segment.kind == "code" else _text_label(self.markdown)
                self._layout.addWidget(widget)
                self._widgets.append(widget)
                self._kinds.append(segment.kind)
            if segment.kind == "code":
                widget.set_code(segment.content, segment.language)
            else:
                widget.setText(segment.content)

    def code_blocks(self) -> list[CodeBlock]:
        return [w for w in self._widgets if isinstance(w, CodeBlock)]

    def _clear(self) -> None:
        for widget in self._widgets:
            self._layout.removeWidget(widget)
            widget.deleteLater()
        self._widgets, self._kinds = [], []
