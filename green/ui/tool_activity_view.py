"""Tool activity: inspect recent tool requests, permission decisions and real outcomes."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..app.bootstrap import AppServices

DECISION_LABELS = {
    "allowed": "Allowed (read tool)",
    "approved_by_user": "Approved by you",
    "declined": "Declined by you",
    "denied": "Denied by permissions",
    "rejected": "Rejected (invalid request)",
    "cancelled": "Cancelled",
}
OUTCOME_LABELS = {"ok": "Succeeded", "error": "Failed", "not_run": "Not run", "invalid_arguments": "Not run",
                  "policy": "Not run"}
COLUMNS = ["Time", "Tool", "Permission decision", "Result", "Summary", "Details", "Duration"]


class ToolActivityView(QWidget):
    def __init__(self, services: AppServices, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)
        title = QLabel("Tool activity")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        intro = QLabel(
            "Every tool GREEN was asked to use, what the permission check decided and what actually happened. "
            "Results come from the tool itself, not from the model. File contents and messages are never stored here."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        filters = QHBoxLayout()
        self.tool_filter = QComboBox()
        self.tool_filter.addItem("All tools", None)
        for tool in services.registry.tools():
            self.tool_filter.addItem(tool.name, tool.name)
        self.tool_filter.currentIndexChanged.connect(self.refresh)
        self.failures_only = QCheckBox("Only failed or blocked")
        self.failures_only.toggled.connect(self.refresh)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        filters.addWidget(self.tool_filter)
        filters.addWidget(self.failures_only)
        filters.addStretch(1)
        filters.addWidget(refresh)
        layout.addLayout(filters)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        for col in range(len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.empty = QLabel("No tool activity yet.")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)

        bottom = QHBoxLayout()
        self.count = QLabel()
        self.count.setObjectName("Muted")
        bottom.addWidget(self.count, 1)
        self.clear_button = QPushButton("Clear history…")
        self.clear_button.setObjectName("DangerButton")
        self.clear_button.clicked.connect(self._clear)
        bottom.addWidget(self.clear_button)
        layout.addLayout(bottom)
        self.refresh()

    def refresh(self) -> None:
        entries = self.services.audit.recent(
            300, failures_only=self.failures_only.isChecked(), tool_name=self.tool_filter.currentData()
        )
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [
                f"{entry.timestamp.astimezone():%d %b %H:%M:%S}",
                entry.tool_name,
                DECISION_LABELS.get(entry.decision, entry.decision),
                OUTCOME_LABELS.get(entry.outcome, entry.outcome),
                entry.summary,
                entry.detail,
                f"{entry.duration_ms} ms",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if col == 3:
                    item.setData(Qt.ItemDataRole.UserRole, entry.outcome)
                self.table.setItem(row, col, item)
        self.table.resizeRowsToContents()
        self.empty.setVisible(not entries)
        self.count.setText(f"Showing {len(entries)} most recent record(s)")
        self.clear_button.setEnabled(bool(entries) or self.failures_only.isChecked())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.table.resizeRowsToContents()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.table.resizeRowsToContents()

    def _clear(self) -> None:
        answer = QMessageBox.warning(
            self,
            "Clear tool history",
            "Delete all tool activity records? This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.services.audit.clear()
            self.refresh()
