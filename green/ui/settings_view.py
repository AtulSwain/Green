"""Settings screen. Values are validated with the same Pydantic models used at load time."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from pydantic import ValidationError

from ..app.bootstrap import AppServices
from ..config import FALLBACK_MODEL, AppConfig, OllamaSettings, format_validation_error
from ..services.ollama_service import CancelToken, GenerationCancelled, OllamaError
from .workers import TaskRunner


def _spin(minimum: int, maximum: int, step: int = 1) -> QSpinBox:
    box = QSpinBox()
    box.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    return box


def _dspin(minimum: float, maximum: float, step: float, decimals: int = 2) -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setDecimals(decimals)
    return box


class SettingsView(QWidget):
    settings_saved = Signal()

    def __init__(self, services: AppServices, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        self.runner = runner
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        title = QLabel("Settings")
        title.setObjectName("PageTitle")
        outer.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("Page")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 12, 0)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # Model
        model_box = QGroupBox("Local model (Ollama)")
        form = QFormLayout(model_box)
        self.base_url = QLineEdit()
        self.base_url.textChanged.connect(self._update_privacy_notice)
        form.addRow("Ollama URL", self.base_url)
        self.privacy_notice = QLabel()
        self.privacy_notice.setObjectName("WarningText")
        self.privacy_notice.setWordWrap(True)
        form.addRow("", self.privacy_notice)
        model_row = QHBoxLayout()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMinimumWidth(220)
        model_row.addWidget(self.model, 1)
        self.refresh_models = QPushButton("Refresh list")
        self.refresh_models.setToolTip("List the models installed in Ollama")
        self.refresh_models.clicked.connect(self._load_models)
        model_row.addWidget(self.refresh_models)
        self.download_model = QPushButton("Download model…")
        self.download_model.setToolTip("Ask Ollama to download the model named in the box (asks first)")
        self.download_model.clicked.connect(self._download_model)
        model_row.addWidget(self.download_model)
        form.addRow("Model", model_row)
        self.model_status = QLabel()
        self.model_status.setObjectName("Muted")
        self.model_status.setWordWrap(True)
        form.addRow("", self.model_status)
        self.timeout = _spin(5, 3600, 10)
        self.timeout.setSuffix(" s")
        form.addRow("Response timeout", self.timeout)
        self.temperature = _dspin(0.0, 2.0, 0.05)
        form.addRow("Temperature", self.temperature)
        self.top_p = _dspin(0.01, 1.0, 0.05)
        form.addRow("Top-p", self.top_p)
        self.num_ctx = _spin(512, 262144, 1024)
        self.num_ctx.setToolTip("Context window in tokens. Larger values need more memory.")
        form.addRow("Context length", self.num_ctx)
        self.num_predict = _spin(-1, 32768, 256)
        self.num_predict.setSpecialValueText("Model default")
        form.addRow("Max response tokens", self.num_predict)
        self.think = QCheckBox("Enable reasoning mode for models that support it (slower, often better)")
        form.addRow("", self.think)
        self.keep_alive = QLineEdit()
        self.keep_alive.setToolTip("How long Ollama keeps the model loaded after a request, e.g. 5m, 1h, 0")
        form.addRow("Keep model loaded", self.keep_alive)
        layout.addWidget(model_box)

        # Context
        context_box = QGroupBox("Conversation context")
        form = QFormLayout(context_box)
        self.history = _spin(2, 200)
        form.addRow("Recent messages sent to the model", self.history)
        self.memory_items = _spin(0, 50)
        form.addRow("Relevant memories per request", self.memory_items)
        self.tool_rounds = _spin(0, 10)
        form.addRow("Max tool steps per response", self.tool_rounds)
        layout.addWidget(context_box)

        # Appearance
        ui_box = QGroupBox("Appearance and input")
        form = QFormLayout(ui_box)
        self.theme = QComboBox()
        self.theme.addItem("Dark", "dark")
        self.theme.addItem("Light", "light")
        form.addRow("Theme", self.theme)
        self.font_size = _spin(8, 20)
        self.font_size.setSuffix(" pt")
        form.addRow("Text size", self.font_size)
        self.send_on_enter = QCheckBox("Enter sends the message (Shift+Enter for a new line)")
        form.addRow("", self.send_on_enter)
        self.close_to_tray = QCheckBox("Keep GREEN running in the system tray when the window is closed")
        self.close_to_tray.setToolTip("Reminders keep working. Quit from the tray icon's menu.")
        form.addRow("", self.close_to_tray)
        layout.addWidget(ui_box)

        voice_note = QLabel("Voice settings (talking to GREEN and its spoken answers) are on the Voice screen.")
        voice_note.setObjectName("Muted")
        layout.addWidget(voice_note)

        # Data
        data_box = QGroupBox("Data and privacy")
        data_layout = QVBoxLayout(data_box)
        privacy = QLabel(
            "• Model inference: performed by Ollama at the URL above (local by default).\n"
            "• Storage: conversations, memories and reminders are kept in a local SQLite database.\n"
            "• Internet: GREEN itself makes no network requests other than to Ollama. Downloading a model "
            "(only when you click Download) and opening approved websites use the internet.\n"
            "• No analytics, telemetry or cloud AI services are used."
        )
        privacy.setWordWrap(True)
        data_layout.addWidget(privacy)
        paths = services.paths
        locations = QLabel(f"Settings: {paths.config_file}\nData: {paths.data_dir}")
        locations.setObjectName("Mono")
        locations.setWordWrap(True)
        locations.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        data_layout.addWidget(locations)
        open_data = QPushButton("Open data folder")
        open_data.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.data_dir))))
        row = QHBoxLayout()
        row.addWidget(open_data)
        row.addStretch(1)
        data_layout.addLayout(row)
        layout.addWidget(data_box)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        self.error_label = QLabel()
        self.error_label.setObjectName("ErrorText")
        self.error_label.setWordWrap(True)
        buttons.addWidget(self.error_label, 1)
        revert = QPushButton("Revert")
        revert.setToolTip("Discard unsaved changes")
        revert.clicked.connect(self.load)
        save = QPushButton("Save settings")
        save.setObjectName("PrimaryButton")
        save.clicked.connect(self.save)
        buttons.addWidget(revert)
        buttons.addWidget(save)
        outer.addLayout(buttons)
        self.load()

    # -- load/save -------------------------------------------------------------------------------
    def load(self) -> None:
        config = self.services.config_store.get()
        o = config.ollama
        self.base_url.setText(o.base_url)
        if self.model.findText(o.model) < 0:
            self.model.addItem(o.model)
        self.model.setCurrentText(o.model)
        self.timeout.setValue(int(o.request_timeout_seconds))
        self.temperature.setValue(o.temperature)
        self.top_p.setValue(o.top_p)
        self.num_ctx.setValue(o.num_ctx)
        self.num_predict.setValue(o.num_predict)
        self.think.setChecked(o.think)
        self.keep_alive.setText(o.keep_alive)
        c = config.context
        self.history.setValue(c.history_messages)
        self.memory_items.setValue(c.memory_items)
        self.tool_rounds.setValue(c.max_tool_rounds)
        self.theme.setCurrentIndex(self.theme.findData(config.ui.theme))
        self.font_size.setValue(config.ui.font_point_size)
        self.send_on_enter.setChecked(config.ui.send_on_enter)
        self.close_to_tray.setChecked(config.ui.close_to_tray)
        self.error_label.setText("")
        self._update_privacy_notice()

    def collect(self) -> AppConfig:
        """Build a validated config from the form. Raises ValidationError."""
        data = self.services.config_store.get().model_dump()
        data["ollama"].update(
            base_url=self.base_url.text(),
            model=self.model.currentText(),
            request_timeout_seconds=self.timeout.value(),
            temperature=self.temperature.value(),
            top_p=self.top_p.value(),
            num_ctx=self.num_ctx.value(),
            num_predict=self.num_predict.value(),
            think=self.think.isChecked(),
            keep_alive=self.keep_alive.text().strip(),
        )
        data["context"].update(
            history_messages=self.history.value(),
            memory_items=self.memory_items.value(),
            max_tool_rounds=self.tool_rounds.value(),
        )
        data["ui"].update(
            theme=self.theme.currentData(), font_point_size=self.font_size.value(), send_on_enter=self.send_on_enter.isChecked(),
            close_to_tray=self.close_to_tray.isChecked(),
        )
        return AppConfig.model_validate(data)

    def save(self) -> bool:
        try:
            config = self.collect()
        except ValidationError as exc:
            self.error_label.setText("Not saved: " + format_validation_error(exc))
            return False
        if not config.ollama.is_local and self.services.config_store.get().ollama.is_local:
            answer = QMessageBox.warning(
                self,
                "Remote model server",
                f"{config.ollama.base_url} is not on this computer. Your messages, relevant memories and tool "
                "results will be sent to that server. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        try:
            self.services.config_store.save(config)
        except OSError as exc:
            self.error_label.setText(f"Could not write the settings file: {exc}")
            return False
        self.error_label.setText("")
        self.settings_saved.emit()
        return True

    def _update_privacy_notice(self) -> None:
        try:
            local = OllamaSettings(base_url=self.base_url.text()).is_local
        except ValidationError:
            self.privacy_notice.setText("Enter a valid http(s) URL, for example http://127.0.0.1:11434")
            return
        self.privacy_notice.setText(
            "" if local else "This address is not on this computer: conversation content would leave your machine."
        )

    # -- model management -----------------------------------------------------------------------
    def _load_models(self) -> None:
        self.refresh_models.setEnabled(False)
        self.model_status.setText("Checking Ollama…")

        def done(status) -> None:
            self.refresh_models.setEnabled(True)
            current = self.model.currentText()
            self.model.clear()
            for info in status.installed_models:
                self.model.addItem(info.name)
            self.model.setCurrentText(current)
            if not status.reachable:
                self.model_status.setText(status.error)
            elif not status.installed_models:
                self.model_status.setText(
                    f"Ollama is running but no models are installed. Recommended: ollama pull qwen3:8b "
                    f"(or {FALLBACK_MODEL} for computers with less memory)."
                )
            else:
                self.model_status.setText(
                    f"Ollama {status.version}: {len(status.installed_models)} model(s) installed. "
                    + ("Configured model is installed." if status.model_installed else
                       f"The saved model '{status.model}' is not installed.")
                )

        def failed(exc) -> None:
            self.refresh_models.setEnabled(True)
            self.model_status.setText(f"Could not query Ollama: {exc}")

        self.runner.submit(self.services.ollama.status, on_done=done, on_error=failed)

    def _download_model(self) -> None:
        name = self.model.currentText().strip()
        try:
            OllamaSettings(model=name)
        except ValidationError:
            QMessageBox.warning(self, "Download model", "Enter a valid model name first, for example qwen3:8b.")
            return
        answer = QMessageBox.question(
            self,
            "Download model",
            f"Ask Ollama to download '{name}'?\n\nModels are large (qwen3:8b is about 5 GB, qwen3:4b about 2.5 GB) "
            "and are downloaded by Ollama from its model registry over the internet.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        cancel = CancelToken()
        progress = QProgressDialog(f"Downloading {name}…", "Cancel", 0, 100, self)
        progress.setWindowTitle("Download model")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.canceled.connect(cancel.cancel)
        progress.setValue(0)

        def work(report):
            self.services.ollama.pull_model(name, cancel, lambda status, done, total: report((status, done, total)))

        def on_progress(payload) -> None:
            status, done, total = payload
            progress.setLabelText(f"{name}: {status}")
            if total:
                progress.setValue(int(done * 100 / total))

        def finished(_) -> None:
            progress.reset()
            self.model_status.setText(f"Downloaded {name}.")
            self._load_models()

        def failed(exc) -> None:
            progress.reset()
            if isinstance(exc, GenerationCancelled):
                self.model_status.setText("Download cancelled.")
            elif isinstance(exc, OllamaError):
                self.model_status.setText(f"Download failed: {exc}")
            else:
                self.model_status.setText("Download failed. Details are in the log.")

        self.runner.submit(work, on_done=finished, on_error=failed, on_progress=on_progress)
