"""Reminders screen."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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
from ..models import utc_now
from .dialogs import ReminderDialog


class RemindersView(QWidget):
    def __init__(self, services: AppServices, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)
        title = QLabel("Reminders")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        intro = QLabel(
            "Reminders are stored locally and shown while GREEN is running. "
            "GREEN does not register anything with Windows Task Scheduler, so reminders due while it is closed "
            "are shown the next time you open it."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.show_completed = QCheckBox("Show completed")
        self.show_completed.toggled.connect(self.refresh)
        layout.addWidget(self.show_completed)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Reminder", "Due", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(lambda _: self._edit())
        layout.addWidget(self.table, 1)
        self.empty = QLabel("No reminders. Create one here, or ask GREEN (requires the create_reminder tool).")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)

        buttons = QHBoxLayout()
        add = QPushButton("New reminder…")
        add.setObjectName("PrimaryButton")
        add.clicked.connect(self._add)
        self.edit_button = QPushButton("Edit…")
        self.edit_button.clicked.connect(self._edit)
        self.complete_button = QPushButton("Mark complete")
        self.complete_button.clicked.connect(self._toggle_complete)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("DangerButton")
        self.delete_button.clicked.connect(self._delete)
        for button in (add, self.edit_button, self.complete_button, self.delete_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        reminders = self.services.reminders.list(include_completed=self.show_completed.isChecked())
        now = utc_now()
        self.table.setRowCount(len(reminders))
        for row, reminder in enumerate(reminders):
            item = QTableWidgetItem(reminder.title)
            item.setData(Qt.ItemDataRole.UserRole, (reminder.id, reminder.completed))
            item.setToolTip(reminder.notes or reminder.title)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem(f"{reminder.due_at.astimezone():%a %d %b %Y  %H:%M}"))
            status = "Completed" if reminder.completed else ("Overdue" if reminder.due_at <= now else "Scheduled")
            self.table.setItem(row, 2, QTableWidgetItem(status))
        self.empty.setVisible(not reminders)
        self._update_buttons()

    def _selected(self) -> tuple[str, bool] | None:
        row = self.table.currentRow()
        if row < 0 or not self.table.selectedItems():
            return None
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def _update_buttons(self) -> None:
        selected = self._selected()
        for button in (self.edit_button, self.complete_button, self.delete_button):
            button.setEnabled(selected is not None)
        self.complete_button.setText("Mark incomplete" if selected and selected[1] else "Mark complete")

    def _add(self) -> None:
        dialog = ReminderDialog(self)
        if dialog.exec():
            title, due, notes = dialog.values()
            self.services.reminders.create(title, due, notes)
            self.refresh()

    def _edit(self) -> None:
        selected = self._selected()
        if not selected:
            return
        dialog = ReminderDialog(self, self.services.reminders.get(selected[0]))
        if dialog.exec():
            title, due, notes = dialog.values()
            self.services.reminders.update(selected[0], title, due, notes)
            self.refresh()

    def _toggle_complete(self) -> None:
        selected = self._selected()
        if selected:
            self.services.reminders.set_completed(selected[0], not selected[1])
            self.refresh()

    def _delete(self) -> None:
        selected = self._selected()
        if selected and QMessageBox.question(self, "Delete reminder", "Delete the selected reminder?") == QMessageBox.StandardButton.Yes:
            self.services.reminders.delete(selected[0])
            self.refresh()
