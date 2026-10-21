"""Memory management screen: view, search, add, edit, delete, clear."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..app.bootstrap import AppServices
from ..models import MemoryCategory
from .dialogs import MemoryDialog


class MemoryView(QWidget):
    def __init__(self, services: AppServices, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)

        title = QLabel("Memory")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        intro = QLabel(
            "Information GREEN may use in future conversations. Nothing is saved without your approval, "
            "and only memories relevant to a request are sent to the local model."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        controls = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search memories…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        controls.addWidget(self.search, 1)
        self.category = QComboBox()
        self.category.addItem("All categories", None)
        for cat in MemoryCategory:
            self.category.addItem(cat.label, cat.value)
        self.category.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.category)
        layout.addLayout(controls)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Content", "Category", "Tags", "Updated"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(lambda _: self._edit())
        layout.addWidget(self.table, 1)
        self.empty = QLabel("No memories yet. Use “Add memory”, or click “Remember…” on any message.")
        self.empty.setObjectName("Muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)

        buttons = QHBoxLayout()
        add = QPushButton("Add memory…")
        add.setObjectName("PrimaryButton")
        add.clicked.connect(self._add)
        self.edit_button = QPushButton("Edit…")
        self.edit_button.clicked.connect(self._edit)
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._delete)
        self.clear_button = QPushButton("Clear all memory…")
        self.clear_button.setObjectName("DangerButton")
        self.clear_button.clicked.connect(self._clear_all)
        for button in (add, self.edit_button, self.delete_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.count_label = QLabel()
        self.count_label.setObjectName("Muted")
        buttons.addWidget(self.count_label)
        buttons.addWidget(self.clear_button)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        records = self.services.memory.search(self.search.text(), self.category.currentData())
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            content = QTableWidgetItem(record.content)
            content.setData(Qt.ItemDataRole.UserRole, record.id)
            content.setToolTip(record.content)
            self.table.setItem(row, 0, content)
            self.table.setItem(row, 1, QTableWidgetItem(record.category.label))
            self.table.setItem(row, 2, QTableWidgetItem(", ".join(record.tags)))
            self.table.setItem(row, 3, QTableWidgetItem(f"{record.updated_at.astimezone():%d %b %Y %H:%M}"))
        self.table.resizeRowsToContents()
        total = self.services.memory.count()
        self.count_label.setText(f"{total} memor{'y' if total == 1 else 'ies'}")
        self.empty.setVisible(not records)
        self.empty.setText(
            "No memories match your search." if total and not records
            else "No memories yet. Use “Add memory”, or click “Remember…” on any message."
        )
        self.clear_button.setEnabled(total > 0)
        self._update_buttons()

    def _selected_id(self) -> str | None:
        row = self.table.currentRow()
        if row < 0 or not self.table.selectedItems():
            return None
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def _update_buttons(self) -> None:
        has = self._selected_id() is not None
        self.edit_button.setEnabled(has)
        self.delete_button.setEnabled(has)

    def _add(self) -> None:
        dialog = MemoryDialog(self)
        if dialog.exec():
            self.services.memory.add(*dialog.values())
            self.refresh()

    def _edit(self) -> None:
        memory_id = self._selected_id()
        if not memory_id:
            return
        record = self.services.memory.get(memory_id)
        dialog = MemoryDialog(self, record=record)
        if dialog.exec():
            content, category, tags = dialog.values()
            self.services.memory.update(memory_id, content, category, tags)
            self.refresh()

    def _delete(self) -> None:
        memory_id = self._selected_id()
        if not memory_id:
            return
        if QMessageBox.question(self, "Delete memory", "Delete the selected memory?") == QMessageBox.StandardButton.Yes:
            self.services.memory.delete(memory_id)
            self.refresh()

    def _clear_all(self) -> None:
        total = self.services.memory.count()
        answer = QMessageBox.warning(
            self,
            "Clear all memory",
            f"Permanently delete all {total} memories? Conversations are not affected. This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.services.memory.clear_all()
            self.refresh()
