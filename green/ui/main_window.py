"""Main application window."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QWidget,
)

from .. import __app_name__, __version__
from ..app.bootstrap import AppServices
from ..models import Reminder
from ..services.ollama_service import OllamaStatus
from ..services.tool_registry import ConfirmationRequest
from .chat_controller import ChatController
from .chat_view import ChatView
from .icon import make_app_icon
from .speech import SpeechOutput
from .dialogs import ConfirmationDialog
from .diagnostics_view import DiagnosticsView
from .memory_view import MemoryView
from .permissions_view import PermissionsView
from .reminders_view import RemindersView
from .settings_view import SettingsView
from .tool_activity_view import ToolActivityView
from .sidebar import Sidebar
from .styles import build_stylesheet, repolish, theme_for
from .voice_view import VoiceView
from .workers import TaskRunner
from .workspace_view import WorkspaceView

log = logging.getLogger(__name__)

STATUS_INTERVAL_MS = 30_000
REMINDER_INTERVAL_MS = 15_000


class MainWindow(QMainWindow):
    def __init__(self, services: AppServices, *, start_timers: bool = True):
        super().__init__()
        self.services = services
        self.setWindowTitle(f"{__app_name__} {__version__}")
        self.setMinimumSize(960, 640)
        self.resize(1240, 820)
        self.runner = TaskRunner(self)
        self.controller = ChatController(services.engine, services.conversations, self.runner, self._confirm, self)
        # Memories saved through the save_memory tool record the conversation they came from.
        services.tool_context.current_conversation = lambda: self.controller.active_conversation
        self._status_check_running = False
        self._open_reminders: dict[str, QMessageBox] = {}
        self._active_dialogs: set = set()
        self._shutting_down = False

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.sidebar = Sidebar(services.conversations)
        layout.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.speech = SpeechOutput(services, self.runner, parent=self)
        self.chat_view = ChatView(services, self.controller, self.runner, self.current_theme, self.speech)
        self.memory_view = MemoryView(services)
        self.reminders_view = RemindersView(services)
        self.workspace_view = WorkspaceView(services, self.runner)
        self.permissions_view = PermissionsView(services)
        self.activity_view = ToolActivityView(services)
        self.voice_view = VoiceView(services, self.runner, self.speech)
        self.settings_view = SettingsView(services, self.runner)
        self.diagnostics_view = DiagnosticsView(services, self.runner)
        self.pages = {
            "chat": self.chat_view,
            "memory": self.memory_view,
            "reminders": self.reminders_view,
            "workspace": self.workspace_view,
            "permissions": self.permissions_view,
            "activity": self.activity_view,
            "voice": self.voice_view,
            "settings": self.settings_view,
            "diagnostics": self.diagnostics_view,
        }
        for page in self.pages.values():
            self.stack.addWidget(page)

        # Status bar: real connection state of Ollama and the configured model.
        self.status_dot = QLabel()
        self.status_dot.setObjectName("StatusDot")
        self.status_text = QLabel("Checking Ollama…")
        self.status_check = QPushButton("Check now")
        self.status_check.setObjectName("LinkButton")
        self.status_check.clicked.connect(self.check_status)
        bar = self.statusBar()
        bar.addWidget(self.status_dot)
        bar.addWidget(self.status_text, 1)
        bar.addPermanentWidget(self.status_check)

        self.sidebar.navigate.connect(self.show_page)
        self.sidebar.new_conversation_requested.connect(self.new_conversation)
        self.sidebar.conversation_selected.connect(self._select_conversation)
        self.sidebar.conversation_deleted.connect(self._conversation_deleted)
        self.sidebar.conversation_renamed.connect(lambda _id: self.chat_view.refresh_title())
        self.chat_view.conversation_created.connect(lambda conv_id: self.sidebar.reload(conv_id))
        self.chat_view.conversation_changed.connect(lambda: self.sidebar.reload(self.chat_view.conversation_id))
        self.controller.busy_changed.connect(self._on_busy)
        self.controller.tool_event.connect(self._on_tool_event)
        self.settings_view.settings_saved.connect(self._settings_saved)
        self.workspace_view.ask_about.connect(self._ask_about_file)
        self.chat_view.model_switched.connect(lambda _name: self.check_status())
        self.voice_view.voice_settings_changed.connect(self.chat_view.apply_config)

        QShortcut(QKeySequence("Ctrl+N"), self, activated=self.new_conversation)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: (self.show_page("chat"), self.chat_view.input.setFocus()))
        QShortcut(QKeySequence("Ctrl+,"), self, activated=lambda: self.show_page("settings"))

        self.apply_theme()
        self.sidebar.reload()
        self.show_page("chat")

        self.app_icon = make_app_icon()
        self.setWindowIcon(self.app_icon)
        self._quitting = False
        self._tray_hint_shown = False
        # Set by the application entry point: closing the window (not hiding it to the tray) ends GREEN.
        self.quit_app_on_close = False
        self.tray = self._build_tray()

        self.status_timer = QTimer(self)
        self.status_timer.setInterval(STATUS_INTERVAL_MS)
        self.status_timer.timeout.connect(self.check_status)
        self.reminder_timer = QTimer(self)
        self.reminder_timer.setInterval(REMINDER_INTERVAL_MS)
        self.reminder_timer.timeout.connect(self.check_reminders)
        if start_timers:
            self.status_timer.start()
            self.reminder_timer.start()
            QTimer.singleShot(0, self.check_status)
            QTimer.singleShot(1500, self.check_reminders)

    # -- theme ------------------------------------------------------------------------------------
    def current_theme(self):
        return theme_for(self.services.config_store.get().ui.theme)

    def apply_theme(self) -> None:
        config = self.services.config_store.get()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(theme_for(config.ui.theme), config.ui.font_point_size))

    # -- navigation --------------------------------------------------------------------------------
    def show_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        if key == "memory":
            self.memory_view.refresh()
        elif key == "reminders":
            self.reminders_view.refresh()
        elif key == "permissions":
            self.permissions_view.refresh()
        elif key == "activity":
            self.activity_view.refresh()
        elif key == "settings":
            self.settings_view.load()
        self.stack.setCurrentWidget(page)
        self.sidebar.set_active_page(key)

    def new_conversation(self) -> None:
        self.sidebar.clear_selection()
        self.chat_view.show_conversation(None)
        self.show_page("chat")
        self.chat_view.input.setFocus()

    def _select_conversation(self, conversation_id: str | None) -> None:
        self.chat_view.show_conversation(conversation_id)
        self.show_page("chat")

    def _conversation_deleted(self, conversation_id: str) -> None:
        if self.controller.active_conversation == conversation_id:
            self.controller.stop()
        if self.chat_view.conversation_id == conversation_id:
            self.chat_view.show_conversation(None)

    def _ask_about_file(self, prompt: str) -> None:
        self.show_page("chat")
        self.chat_view.input.setPlainText(prompt)
        self.chat_view.input.setFocus()

    def _settings_saved(self) -> None:
        self.apply_theme()
        self.chat_view.apply_config()
        self.voice_view.refresh()
        self.statusBar().showMessage("Settings saved.", 3000)
        self.check_status()

    def _on_busy(self, busy: bool) -> None:
        self.status_check.setEnabled(not busy)

    # -- tool confirmations (GUI thread, non-blocking) -------------------------------------------------
    def _confirm(self, request: ConfirmationRequest, respond):
        """Show an approval dialog without blocking the event loop. Returns a closer used by Stop."""
        if self._shutting_down:
            respond(False)
            return None
        dialog = ConfirmationDialog(request, self, self.current_theme())
        dialog.setWindowModality(Qt.WindowModality.NonModal)  # Stop and the rest of GREEN stay usable
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        answered = [False]

        def finished(code: int) -> None:
            if not answered[0]:
                answered[0] = True
                respond(code == QDialog.DialogCode.Accepted)
            self._active_dialogs.discard(dialog)

        def close() -> None:
            answered[0] = True  # the controller already recorded the decline
            dialog.reject()

        dialog.finished.connect(finished)
        self._active_dialogs.add(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        QApplication.alert(self)
        return close

    def _on_tool_event(self, _conversation_id: str, event) -> None:
        if event.phase == "finished" and self.stack.currentWidget() is self.activity_view:
            self.activity_view.refresh()

    # -- model status ---------------------------------------------------------------------------------------
    def check_status(self) -> None:
        if self._status_check_running or self._shutting_down:
            return
        self._status_check_running = True
        self.runner.submit(self.services.ollama.status, on_done=self._show_status, on_error=self._status_failed)

    def _status_failed(self, error: Exception) -> None:
        self._status_check_running = False
        self._set_status("error", "Could not check Ollama status.")

    def _show_status(self, status: OllamaStatus) -> None:
        self._status_check_running = False
        if status.reachable:
            self.chat_view.set_models([m.name for m in status.installed_models], status.model)
        remote = "" if status.is_local else "  ·  REMOTE server: messages leave this computer"
        if not status.reachable:
            self._set_status("error", f"Ollama not reachable at {status.base_url}. Start Ollama, then click Check now.")
        elif not status.model_installed:
            self._set_status("warning", f"Ollama {status.version} connected · model '{status.model}' not installed — run: ollama pull {status.model}{remote}")
        elif status.supports_tools is False:
            self._set_status("warning", f"Ollama {status.version} · {status.model} ready · this model does not support tools{remote}")
        else:
            self._set_status("ok", f"Ollama {status.version} · {status.model} ready{remote}")
        self.chat_view.set_model_ready(status.ready)

    def _set_status(self, state: str, text: str) -> None:
        self.status_dot.setProperty("state", state)
        repolish(self.status_dot)
        self.status_text.setText(text)
        self.status_dot.setToolTip(text)

    # -- reminders ----------------------------------------------------------------------------------------------
    def check_reminders(self) -> None:
        if self._shutting_down:
            return
        try:
            due = self.services.reminders.due()
        except Exception:
            log.exception("Reminder check failed")
            return
        for reminder in due:
            self.services.reminders.mark_notified(reminder.id)
            self._show_reminder(reminder)
        if due and self.stack.currentWidget() is self.reminders_view:
            self.reminders_view.refresh()

    def _show_reminder(self, reminder: Reminder) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("GREEN reminder")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(reminder.title)
        when = reminder.due_at.astimezone().strftime("%a %d %b %Y, %H:%M")
        box.setInformativeText(f"Due {when}" + (f"\n\n{reminder.notes}" if reminder.notes else ""))
        complete = box.addButton("Mark complete", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Dismiss", QMessageBox.ButtonRole.RejectRole)
        box.setWindowModality(Qt.WindowModality.NonModal)

        def finished(_result) -> None:
            if box.clickedButton() is complete:
                try:
                    self.services.reminders.set_completed(reminder.id, True)
                except LookupError:
                    pass
                if self.stack.currentWidget() is self.reminders_view:
                    self.reminders_view.refresh()
            self._open_reminders.pop(reminder.id, None)

        box.finished.connect(finished)
        self._open_reminders[reminder.id] = box
        box.show()
        QApplication.alert(self)
        if self.tray is not None:
            self.tray.showMessage("GREEN reminder", reminder.title, self.app_icon, 15000)

    # -- tray -----------------------------------------------------------------------------------------------
    def _build_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self.app_icon, self)
        tray.setToolTip("GREEN")
        menu = QMenu(self)
        menu.addAction("Open GREEN", self.bring_to_front)
        menu.addAction("New conversation", lambda: (self.bring_to_front(), self.new_conversation()))
        menu.addSeparator()
        menu.addAction("Quit GREEN", self.quit_app)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: self.bring_to_front() if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
        tray.show()
        return tray

    def bring_to_front(self) -> None:
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._quitting = True
        self.close()
        if self._shutting_down:
            QApplication.quit()

    # -- shutdown -------------------------------------------------------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if (not self._quitting and self.tray is not None and not self._shutting_down
                and self.services.config_store.get().ui.close_to_tray):
            event.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.showMessage("GREEN is still running", "Reminders keep working. Right-click the tray icon to quit.",
                                      self.app_icon, 5000)
            return
        if self.controller.is_busy and not self._shutting_down:
            answer = QMessageBox.question(
                self,
                "Quit GREEN",
                "A response is still being generated. Stop it and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.shutdown()
        event.accept()
        if self.quit_app_on_close:
            QApplication.quit()

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.status_timer.stop()
        self.reminder_timer.stop()
        self.controller.stop()  # also withdraws a pending approval
        self.chat_view.shutdown()
        if self.tray is not None:
            self.tray.hide()
        for dialog in list(self._active_dialogs):
            dialog.reject()
        self.services.voice_output.stop()
        if self.services.voice_input.is_recording:
            try:
                self.services.voice_input.stop_recording()
            except Exception:
                pass
        if not self.runner.shutdown(8000):
            log.warning("Background work did not finish within the shutdown timeout")
        log.info("Main window closed")
