"""Tools & permissions screen: tool toggles, approved locations, allow-lists, audit log."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from pydantic import ValidationError

from ..app.bootstrap import AppServices
from ..config import format_validation_error, normalize_site
from ..services.tool_registry import RESTRICTED_CAPABILITIES, ToolCategory

CATEGORY_LABELS = {ToolCategory.READ: "Read", ToolCategory.WRITE: "Controlled write (asks first)"}


def _is_broad_directory(path: Path) -> bool:
    resolved = path.resolve()
    return resolved == Path(resolved.anchor) or resolved == Path.home().resolve()


class PermissionsView(QWidget):
    def __init__(self, services: AppServices, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)
        title = QLabel("Tools & Permissions")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        intro = QLabel(
            "The model can only request tools you enable here. Every tool call is checked against these "
            "settings, and anything that changes files or opens programs asks you first."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_tools_tab(), "Tools")
        self.tabs.addTab(self._build_folders_tab(), "Folders")
        self.tabs.addTab(self._build_apps_tab(), "Applications && websites")
        self.tabs.addTab(self._build_audit_tab(), "Audit log")
        self.tabs.currentChanged.connect(lambda i: self.refresh_audit() if i == 3 else None)
        layout.addWidget(self.tabs, 1)
        self.refresh()

    # -- helpers -----------------------------------------------------------------------------
    def _save(self, mutate) -> bool:
        try:
            self.services.config_store.update(mutate)
        except ValidationError as exc:
            QMessageBox.warning(self, "Invalid setting", format_validation_error(exc))
            return False
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not save", str(exc))
            return False
        self.refresh()
        return True

    @staticmethod
    def _muted(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        label.setWordWrap(True)
        return label

    # -- tools tab ------------------------------------------------------------------------------
    def _build_tools_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.tool_table = QTableWidget(0, 3)
        self.tool_table.setHorizontalHeaderLabels(["Tool", "Type", "What it does"])
        self.tool_table.verticalHeader().setVisible(False)
        self.tool_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tool_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tool_table.setWordWrap(True)
        header = self.tool_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.tool_table.itemChanged.connect(self._tool_toggled)
        layout.addWidget(self.tool_table, 1)
        restricted = "Always unavailable (restricted): " + "; ".join(RESTRICTED_CAPABILITIES) + "."
        layout.addWidget(self._muted(restricted))
        return page

    def _tool_toggled(self, item: QTableWidgetItem) -> None:
        if item.column() != 0 or self._loading:
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        self.services.permissions.set_enabled(name, enabled)

    # -- folders tab ------------------------------------------------------------------------------
    def _build_folders_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Approved directories (read access for list, search and read tools)"))
        self.dir_list = QListWidget()
        layout.addWidget(self.dir_list, 1)
        row = QHBoxLayout()
        add = QPushButton("Add folder…")
        add.clicked.connect(self._add_directory)
        self.remove_dir = QPushButton("Remove")
        self.remove_dir.clicked.connect(self._remove_directory)
        row.addWidget(add)
        row.addWidget(self.remove_dir)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(self._muted(
            "Protected files and folders are always excluded, even inside approved folders: .env files, keys and "
            "certificates, credential files, .git, .ssh, node_modules, virtual environments and similar."
        ))
        layout.addSpacing(12)
        layout.addWidget(QLabel("Output directory (where create_text_file may create new files)"))
        out_row = QHBoxLayout()
        self.output_label = QLabel()
        self.output_label.setObjectName("Mono")
        self.output_label.setWordWrap(True)
        out_row.addWidget(self.output_label, 1)
        choose = QPushButton("Choose…")
        choose.clicked.connect(self._choose_output)
        self.clear_output = QPushButton("Clear")
        self.clear_output.clicked.connect(lambda: self._save(lambda c: setattr(c.tools, "output_directory", "")))
        out_row.addWidget(choose)
        out_row.addWidget(self.clear_output)
        layout.addLayout(out_row)
        return page

    def _add_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Approve a folder for GREEN to read")
        if not directory:
            return
        if _is_broad_directory(Path(directory)):
            answer = QMessageBox.warning(
                self,
                "Broad folder",
                f"{directory} is a very broad location. GREEN's tools would be able to read most files in it "
                "(protected files excepted). Approve anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._save(lambda c: setattr(c.tools, "approved_directories", [*c.tools.approved_directories, directory]))

    def _remove_directory(self) -> None:
        item = self.dir_list.currentItem()
        if item:
            target = item.text()
            self._save(lambda c: setattr(c.tools, "approved_directories", [d for d in c.tools.approved_directories if d != target]))

    def _choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Choose the output directory for new files")
        if directory:
            self._save(lambda c: setattr(c.tools, "output_directory", directory))

    # -- apps & websites tab ------------------------------------------------------------------------
    def _build_apps_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(QLabel("Approved applications (open_application can start these, after asking you)"))
        self.app_table = QTableWidget(0, 2)
        self.app_table.setHorizontalHeaderLabels(["Name", "Program"])
        self.app_table.verticalHeader().setVisible(False)
        self.app_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.app_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.app_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.app_table, 1)
        row = QHBoxLayout()
        add = QPushButton("Add application…")
        add.clicked.connect(self._add_app)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_app)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addSpacing(12)
        layout.addWidget(QLabel("Allowed websites (open_website can open pages on these sites and their subdomains)"))
        self.site_list = QListWidget()
        layout.addWidget(self.site_list, 1)
        row2 = QHBoxLayout()
        add_site = QPushButton("Add website…")
        add_site.clicked.connect(self._add_site)
        remove_site = QPushButton("Remove")
        remove_site.clicked.connect(self._remove_site)
        row2.addWidget(add_site)
        row2.addWidget(remove_site)
        row2.addStretch(1)
        layout.addLayout(row2)
        return page

    def _add_app(self) -> None:
        program, _ = QFileDialog.getOpenFileName(self, "Choose a program", "", "Programs (*.exe);;All files (*)")
        if not program:
            return
        name, ok = QInputDialog.getText(self, "Application name", "Name GREEN should use for this program:", text=Path(program).stem)
        if ok and name.strip():
            self._save(lambda c: setattr(c.tools, "approved_applications", {**c.tools.approved_applications, name.strip(): program}))

    def _remove_app(self) -> None:
        row = self.app_table.currentRow()
        if row >= 0 and self.app_table.item(row, 0):
            name = self.app_table.item(row, 0).text()
            self._save(lambda c: setattr(c.tools, "approved_applications",
                                         {k: v for k, v in c.tools.approved_applications.items() if k != name}))

    def _add_site(self) -> None:
        text, ok = QInputDialog.getText(self, "Allow website", "Website (for example docs.python.org):")
        if not ok or not text.strip():
            return
        try:
            host = normalize_site(text)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid website", str(exc))
            return
        self._save(lambda c: setattr(c.tools, "allowed_websites", [*c.tools.allowed_websites, host]))

    def _remove_site(self) -> None:
        item = self.site_list.currentItem()
        if item:
            host = item.text()
            self._save(lambda c: setattr(c.tools, "allowed_websites", [s for s in c.tools.allowed_websites if s != host]))

    # -- audit tab ---------------------------------------------------------------------------------------
    def _build_audit_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self._muted("Every tool request, with the permission decision and outcome. File contents are never logged."))
        self.audit_table = QTableWidget(0, 5)
        self.audit_table.setHorizontalHeaderLabels(["Time", "Tool", "Decision", "Outcome", "Detail"])
        self.audit_table.verticalHeader().setVisible(False)
        self.audit_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.audit_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.audit_table, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_audit)
        row = QHBoxLayout()
        row.addWidget(refresh)
        row.addStretch(1)
        layout.addLayout(row)
        return page

    def refresh_audit(self) -> None:
        entries = self.services.audit.recent(200)
        self.audit_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            values = [f"{entry.timestamp.astimezone():%d %b %H:%M:%S}", entry.tool_name, entry.decision, entry.outcome, entry.detail]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.audit_table.setItem(row, col, item)
        self.audit_table.resizeColumnsToContents()
        self.audit_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)

    # -- refresh -------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        config = self.services.config_store.get()
        self._loading = True
        tools = self.services.registry.tools()
        self.tool_table.setRowCount(len(tools))
        for row, tool in enumerate(tools):
            name = QTableWidgetItem(tool.name)
            name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            name.setCheckState(Qt.CheckState.Checked if config.tools.enabled_tools.get(tool.name) else Qt.CheckState.Unchecked)
            name.setData(Qt.ItemDataRole.UserRole, tool.name)
            name.setToolTip("Tick to allow the model to request this tool")
            self.tool_table.setItem(row, 0, name)
            self.tool_table.setItem(row, 1, QTableWidgetItem(CATEGORY_LABELS.get(tool.category, tool.category.value)))
            self.tool_table.setItem(row, 2, QTableWidgetItem(tool.description))
        self.tool_table.resizeRowsToContents()
        self._loading = False

        self.dir_list.clear()
        self.dir_list.addItems(config.tools.approved_directories)
        self.output_label.setText(config.tools.output_directory or "Not set — create_text_file cannot write anywhere.")
        self.clear_output.setEnabled(bool(config.tools.output_directory))

        apps = config.tools.approved_applications
        self.app_table.setRowCount(len(apps))
        for row, (name, path) in enumerate(sorted(apps.items())):
            self.app_table.setItem(row, 0, QTableWidgetItem(name))
            self.app_table.setItem(row, 1, QTableWidgetItem(path))
        self.site_list.clear()
        self.site_list.addItems(config.tools.allowed_websites)
