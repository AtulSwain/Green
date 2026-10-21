"""Diagnostics screen built from real checks."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..app.bootstrap import AppServices
from ..services.diagnostics import DiagnosticsReport, build_report
from .workers import TaskRunner


class DiagnosticsView(QWidget):
    def __init__(self, services: AppServices, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        self.runner = runner
        self.report: DiagnosticsReport | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)
        title = QLabel("Diagnostics")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        note = QLabel("Everything here is checked live. Nothing that was not detected is reported as verified.")
        note.setObjectName("Muted")
        layout.addWidget(note)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["", "Check", "Result"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 2)
        layout.addWidget(QLabel("Recent warnings and errors (redacted)"))
        self.errors = QPlainTextEdit()
        self.errors.setObjectName("CodeView")
        self.errors.setReadOnly(True)
        layout.addWidget(self.errors, 1)
        row = QHBoxLayout()
        self.refresh_button = QPushButton("Run checks")
        self.refresh_button.setObjectName("PrimaryButton")
        self.refresh_button.clicked.connect(self.refresh)
        self.copy_button = QPushButton("Copy report")
        self.copy_button.clicked.connect(self._copy)
        self.copy_button.setEnabled(False)
        self.status = QLabel()
        self.status.setObjectName("Muted")
        row.addWidget(self.refresh_button)
        row.addWidget(self.copy_button)
        row.addWidget(self.status, 1)
        layout.addLayout(row)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.report is None:
            self.refresh()

    def refresh(self) -> None:
        self.refresh_button.setEnabled(False)
        self.status.setText("Running checks…")

        def done(report: DiagnosticsReport) -> None:
            self.report = report
            self.refresh_button.setEnabled(True)
            self.copy_button.setEnabled(True)
            self.status.setText("")
            self.table.setRowCount(len(report.items))
            for row, item in enumerate(report.items):
                mark = "" if item.ok is None else ("OK" if item.ok else "Problem")
                self.table.setItem(row, 0, QTableWidgetItem(mark))
                self.table.setItem(row, 1, QTableWidgetItem(item.label))
                value = QTableWidgetItem(item.value)
                value.setToolTip(item.value)
                self.table.setItem(row, 2, value)
            self.table.resizeRowsToContents()
            self.errors.setPlainText("\n".join(report.recent_errors) or "(none)")

        def failed(exc: Exception) -> None:
            self.refresh_button.setEnabled(True)
            self.status.setText(f"Diagnostics failed: {exc.__class__.__name__}")

        self.runner.submit(lambda: build_report(self.services), on_done=done, on_error=failed)

    def _copy(self) -> None:
        if self.report:
            QGuiApplication.clipboard().setText(self.report.as_text())
            self.status.setText("Report copied to the clipboard.")
