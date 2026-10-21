"""Project workspace: browse, preview and search a user-selected project folder."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..app.bootstrap import AppServices
from ..security.path_policy import PathPolicy, PathPolicyError
from ..services import file_access
from .workers import TaskRunner

MAX_FILES = 5000
MAX_PROMPT_CHARS = 24_000


class WorkspaceView(QWidget):
    ask_about = Signal(str)  # prompt text for the composer

    def __init__(self, services: AppServices, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        self.runner = runner
        self._current_file: Path | None = None
        self._current_text = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)

        title = QLabel("Workspace")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        intro = QLabel(
            "Choose a project folder to browse and search it, and to let GREEN's workspace tools read it. "
            "GREEN never changes project files on its own: proposed edits are shown as a diff for you to approve."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        row = QHBoxLayout()
        self.root_label = QLabel()
        self.root_label.setObjectName("Mono")
        self.root_label.setWordWrap(True)
        row.addWidget(self.root_label, 1)
        choose = QPushButton("Choose project folder…")
        choose.clicked.connect(self._choose)
        self.close_button = QPushButton("Close project")
        self.close_button.clicked.connect(self._close_project)
        row.addWidget(choose)
        row.addWidget(self.close_button)
        layout.addLayout(row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter files…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply_filter)
        left_layout.addWidget(self.filter)
        self.file_list = QListWidget()
        self.file_list.currentItemChanged.connect(lambda item, _: self._open_item(item))
        left_layout.addWidget(self.file_list, 1)
        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search text or symbol…")
        self.search_box.returnPressed.connect(self._search)
        search_row.addWidget(self.search_box, 1)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._search)
        search_row.addWidget(self.search_button)
        left_layout.addLayout(search_row)
        self.results = QListWidget()
        self.results.itemActivated.connect(self._open_result)
        self.results.setToolTip("Press Enter or double-click to open a result")
        left_layout.addWidget(self.results, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.file_label = QLabel("No file selected")
        self.file_label.setObjectName("Muted")
        right_layout.addWidget(self.file_label)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("CodeView")
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        right_layout.addWidget(self.preview, 1)
        ask_row = QHBoxLayout()
        ask_row.addStretch(1)
        self.ask_button = QPushButton("Ask GREEN about this file")
        self.ask_button.setObjectName("PrimaryButton")
        self.ask_button.setToolTip("Put this file into the message box so you can add a question before sending")
        self.ask_button.clicked.connect(self._ask)
        ask_row.addWidget(self.ask_button)
        right_layout.addLayout(ask_row)
        splitter.addWidget(right)
        splitter.setSizes([320, 680])
        layout.addWidget(splitter, 1)
        self.status = QLabel()
        self.status.setObjectName("Muted")
        layout.addWidget(self.status)
        self._all_files: list[str] = []
        self.refresh()

    def _policy(self) -> PathPolicy:
        return PathPolicy([self.services.config_store.get().workspace.project_directory])

    def refresh(self) -> None:
        root = self.services.config_store.get().workspace.project_directory
        self.root_label.setText(root or "No project selected.")
        self.close_button.setEnabled(bool(root))
        self.search_button.setEnabled(bool(root))
        self.search_box.setEnabled(bool(root))
        self.filter.setEnabled(bool(root))
        self.file_list.clear()
        self.results.clear()
        self._all_files = []
        self._show_file(None, "")
        if not root:
            self.status.setText("")
            return
        if not Path(root).is_dir():
            self.status.setText("The project folder no longer exists.")
            return
        self.status.setText("Listing files…")
        policy = self._policy()

        def work():
            base = policy.roots[0]
            files = []
            for path in policy.walk_files(base):
                files.append(path.relative_to(base).as_posix())
                if len(files) >= MAX_FILES:
                    break
            return files

        def done(files):
            self._all_files = files
            self._apply_filter()
            more = " (limit reached)" if len(files) >= MAX_FILES else ""
            self.status.setText(f"{len(files)} files{more}. Protected files and dependency folders are hidden.")

        self.runner.submit(work, on_done=done, on_error=lambda exc: self.status.setText(f"Could not list files: {exc}"))

    def _apply_filter(self) -> None:
        needle = self.filter.text().strip().lower()
        self.file_list.clear()
        for rel in self._all_files:
            if not needle or needle in rel.lower():
                self.file_list.addItem(rel)

    def _choose(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a project folder")
        if folder:
            self.services.config_store.update(lambda c: setattr(c.workspace, "project_directory", folder))
            self.refresh()

    def _close_project(self) -> None:
        self.services.config_store.update(lambda c: setattr(c.workspace, "project_directory", ""))
        self.refresh()

    def _open_item(self, item: QListWidgetItem | None, line: int | None = None) -> None:
        if item is None:
            return
        rel = item.text() if line is None else item.data(Qt.ItemDataRole.UserRole)
        self._open_relative(rel, line)

    def _open_relative(self, rel: str, line: int | None = None) -> None:
        try:
            path = self._policy().resolve(rel)
            content = file_access.read_text(path, self.services.config_store.get().tools.max_read_bytes)
        except (PathPolicyError, file_access.FileAccessError) as exc:
            self._show_file(None, "")
            self.file_label.setText(f"{rel}: {exc}")
            return
        except OSError as exc:
            self._show_file(None, "")
            self.file_label.setText(f"{rel}: could not read file ({exc.strerror})")
            return
        suffix = " (truncated preview)" if content.truncated else ""
        self._show_file(path, content.text, f"{rel}{suffix}")
        if line:
            block = self.preview.document().findBlockByLineNumber(line - 1)
            cursor = QTextCursor(block)
            self.preview.setTextCursor(cursor)
            self.preview.centerCursor()

    def _show_file(self, path: Path | None, text: str, label: str = "No file selected") -> None:
        self._current_file = path
        self._current_text = text
        self.preview.setPlainText(text)
        self.file_label.setText(label)
        self.ask_button.setEnabled(path is not None)

    def _search(self) -> None:
        query = self.search_box.text().strip()
        if not query:
            return
        policy = self._policy()
        if not policy.roots:
            return
        self.results.clear()
        self.status.setText("Searching…")
        self.search_button.setEnabled(False)
        limit = self.services.config_store.get().tools.max_results

        def done(result):
            matches, truncated = result
            self.search_button.setEnabled(True)
            root = policy.roots[0]
            for match in matches:
                rel = match.path.relative_to(root).as_posix()
                item = QListWidgetItem(f"{rel}:{match.line_number}: {match.line.strip()}")
                item.setData(Qt.ItemDataRole.UserRole, rel)
                item.setData(Qt.ItemDataRole.UserRole + 1, match.line_number)
                self.results.addItem(item)
            self.status.setText(f"{len(matches)} match(es){' (limit reached)' if truncated else ''}.")

        def failed(exc):
            self.search_button.setEnabled(True)
            self.status.setText(f"Search failed: {exc}")

        self.runner.submit(lambda: file_access.search_text(policy, policy.roots[0], query, limit), on_done=done, on_error=failed)

    def _open_result(self, item: QListWidgetItem) -> None:
        self._open_relative(item.data(Qt.ItemDataRole.UserRole), item.data(Qt.ItemDataRole.UserRole + 1))

    def _ask(self) -> None:
        if self._current_file is None:
            return
        root = self._policy().roots[0]
        rel = self._current_file.relative_to(root).as_posix()
        text = self._current_text
        if len(text) > MAX_PROMPT_CHARS:
            answer = QMessageBox.question(
                self, "Large file", f"Only the first {MAX_PROMPT_CHARS:,} characters will be included. Continue?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            text = text[:MAX_PROMPT_CHARS] + "\n… (truncated)"
        lang = self._current_file.suffix.lstrip(".")
        self.ask_about.emit(f"Please explain `{rel}`:\n\n```{lang}\n{text}\n```\n")
