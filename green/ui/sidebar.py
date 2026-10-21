"""Sidebar: branding, conversation list and navigation."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..services.conversation_service import ConversationService

NAV_ITEMS = (
    ("chat", "Chat"),
    ("memory", "Memory"),
    ("reminders", "Reminders"),
    ("workspace", "Workspace"),
    ("permissions", "Tools && Permissions"),
    ("voice", "Voice"),
    ("settings", "Settings"),
    ("diagnostics", "Diagnostics"),
)


class Sidebar(QFrame):
    conversation_selected = Signal(object)  # str | None
    conversation_deleted = Signal(str)
    conversation_renamed = Signal(str)
    new_conversation_requested = Signal()
    navigate = Signal(str)

    def __init__(self, conversations: ConversationService, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.conversations = conversations
        self.setFixedWidth(264)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(6)

        brand = QLabel("GREEN")
        brand.setObjectName("Brand")
        layout.addWidget(brand)
        sub = QLabel("Local-first assistant")
        sub.setObjectName("BrandSub")
        layout.addWidget(sub)
        layout.addSpacing(8)

        self.new_button = QPushButton("+  New conversation")
        self.new_button.setObjectName("PrimaryButton")
        self.new_button.setToolTip("Start a new conversation (Ctrl+N)")
        self.new_button.clicked.connect(self.new_conversation_requested)
        layout.addWidget(self.new_button)

        label = QLabel("CONVERSATIONS")
        label.setObjectName("SectionLabel")
        layout.addWidget(label)
        self.list = QListWidget()
        self.list.setObjectName("ConversationList")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.currentItemChanged.connect(self._on_current_changed)
        self.list.setToolTip("Right-click, F2 or Delete to manage a conversation")
        rename_action = QAction("Rename", self.list)
        rename_action.setShortcut(QKeySequence(Qt.Key.Key_F2))
        rename_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        rename_action.triggered.connect(lambda: self._rename(self.list.currentItem()))
        delete_action = QAction("Delete", self.list)
        delete_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Delete))
        delete_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        delete_action.triggered.connect(lambda: self._delete(self.list.currentItem()))
        self.list.addActions([rename_action, delete_action])
        layout.addWidget(self.list, 1)
        self.empty_label = QLabel("No conversations yet.")
        self.empty_label.setObjectName("Muted")
        layout.addWidget(self.empty_label)

        nav_label = QLabel("NAVIGATE")
        nav_label.setObjectName("SectionLabel")
        layout.addWidget(nav_label)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, text in NAV_ITEMS:
            button = QPushButton(text)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self.navigate.emit(k))
            self.nav_group.addButton(button)
            self.nav_buttons[key] = button
            layout.addWidget(button)
        self._suppress = False

    def set_active_page(self, key: str) -> None:
        if key in self.nav_buttons:
            self.nav_buttons[key].setChecked(True)

    def set_voice_visible(self, visible: bool) -> None:
        self.nav_buttons["voice"].setVisible(visible)

    def reload(self, select_id: str | None = None) -> None:
        current = select_id if select_id is not None else self.current_id()
        self._suppress = True
        try:
            self.list.clear()
            for conv in self.conversations.list():
                item = QListWidgetItem(conv.title)
                item.setData(Qt.ItemDataRole.UserRole, conv.id)
                item.setToolTip(f"{conv.title}\nUpdated {conv.updated_at.astimezone():%d %b %Y %H:%M}")
                self.list.addItem(item)
                if conv.id == current:
                    self.list.setCurrentItem(item)
        finally:
            self._suppress = False
        self.empty_label.setVisible(self.list.count() == 0)

    def current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def clear_selection(self) -> None:
        self._suppress = True
        self.list.setCurrentItem(None)
        self.list.clearSelection()
        self._suppress = False

    def _on_current_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if self._suppress or current is None:
            return
        self.conversation_selected.emit(current.data(Qt.ItemDataRole.UserRole))

    def _context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction("Rename…", lambda: self._rename(item))
        menu.addAction("Delete…", lambda: self._delete(item))
        menu.exec(self.list.mapToGlobal(pos))

    def _rename(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        conv_id = item.data(Qt.ItemDataRole.UserRole)
        title, ok = QInputDialog.getText(self, "Rename conversation", "Title:", text=item.text())
        if not ok:
            return
        try:
            self.conversations.rename(conv_id, title)
        except ValueError as exc:
            QMessageBox.warning(self, "Rename conversation", str(exc))
            return
        self.reload()
        self.conversation_renamed.emit(conv_id)

    def _delete(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        conv_id = item.data(Qt.ItemDataRole.UserRole)
        answer = QMessageBox.question(
            self,
            "Delete conversation",
            f"Delete “{item.text()}” and all of its messages? This cannot be undone.\n"
            "Memories saved from it are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.conversations.delete(conv_id)
        self.reload()
        self.conversation_deleted.emit(conv_id)
