"""Voice screen: everything needed to talk to GREEN and hear its answers."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..app.bootstrap import AppServices
from ..services.voice_input_service import VoiceError
from .speech import SpeechOutput
from .workers import TaskRunner

SPEECH_MODELS = (
    ("tiny.en", "fastest, about 75 MB"),
    ("base.en", "recommended, about 145 MB"),
    ("small.en", "more accurate, about 480 MB"),
    ("base", "any language, about 145 MB"),
)
PAUSE_CHOICES = (0.75, 1.0, 1.5, 2.0, 3.0)
TEST_PHRASE = "Hello! This is GREEN. I can read my answers out loud."


class VoiceView(QWidget):
    voice_settings_changed = Signal()

    def __init__(self, services: AppServices, runner: TaskRunner, speech: SpeechOutput, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        self.runner = runner
        self.speech = speech
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        title = QLabel("Voice")
        title.setObjectName("PageTitle")
        outer.addWidget(title)
        intro = QLabel(
            "Talk to GREEN and hear the answer. Everything runs on this computer: the microphone is only used "
            "after you click Talk, and audio is never saved or uploaded."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        outer.addWidget(intro)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body.setObjectName("Page")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 12, 0)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # -- conversation ------------------------------------------------------------------------
        box = QGroupBox("Voice conversation")
        form = QVBoxLayout(box)
        self.enabled = self._check("Show the Talk button in the chat (Ctrl+M)", "enabled")
        self.conversation_mode = self._check("When I talk, send it right away and answer out loud", "conversation_mode")
        self.auto_stop = self._check("Stop listening automatically when I pause", "auto_stop_on_silence")
        silence_row = QHBoxLayout()
        silence_row.addSpacing(26)
        silence_row.addWidget(QLabel("Pause length:"))
        self.silence = QComboBox()
        for seconds in PAUSE_CHOICES:
            self.silence.addItem(f"{seconds:g} seconds", seconds)
        self.silence.activated.connect(lambda _: self._save("silence_seconds", float(self.silence.currentData())))
        silence_row.addWidget(self.silence)
        silence_row.addStretch(1)
        self.speak_all = self._check("Read every reply aloud (also typed messages)", "speak_responses")
        self.muted = self._check("Mute (no automatic speech; “Read aloud” still works)", "muted")
        for widget in (self.enabled, self.conversation_mode, self.auto_stop):
            form.addWidget(widget)
        form.addLayout(silence_row)
        form.addWidget(self.speak_all)
        form.addWidget(self.muted)
        layout.addWidget(box)

        # -- speech output ---------------------------------------------------------------------------
        box = QGroupBox("GREEN's voice")
        out = QFormLayout(box)
        self.engine = QComboBox()
        self.engine.addItem("Windows voices (built in, no download)", "system")
        self.engine.addItem("Piper voice file (more natural, needs a download)", "piper")
        self.engine.activated.connect(lambda _: self._save("tts_engine", self.engine.currentData()))
        out.addRow("Engine", self.engine)
        self.voice = QComboBox()
        self.voice.activated.connect(lambda _: self._save("system_voice", self.voice.currentData() or ""))
        out.addRow("Voice", self.voice)
        self.rate = QSlider(Qt.Orientation.Horizontal)
        self.rate.setRange(-10, 10)
        self.rate.setToolTip("Speaking speed")
        self.rate.valueChanged.connect(lambda v: self._save("speech_rate", v / 10))
        out.addRow("Speed", self.rate)
        piper_row = QHBoxLayout()
        self.piper_path = QLineEdit()
        self.piper_path.setPlaceholderText("Path to a Piper .onnx voice (with its .onnx.json next to it)")
        self.piper_path.editingFinished.connect(lambda: self._save("piper_model_path", self.piper_path.text().strip()))
        piper_row.addWidget(self.piper_path, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_piper)
        piper_row.addWidget(browse)
        self.piper_row_label = QLabel("Piper voice")
        out.addRow(self.piper_row_label, piper_row)
        self._piper_widgets = (self.piper_path, browse, self.piper_row_label)
        buttons = QHBoxLayout()
        self.test_voice = QPushButton("Test voice")
        self.test_voice.setObjectName("PrimaryButton")
        self.test_voice.clicked.connect(lambda: self.speech.speak(TEST_PHRASE, respect_mute=False))
        stop = QPushButton("Stop")
        stop.clicked.connect(self.speech.stop)
        buttons.addWidget(self.test_voice)
        buttons.addWidget(stop)
        buttons.addStretch(1)
        out.addRow("", buttons)
        self.output_status = QLabel()
        self.output_status.setWordWrap(True)
        out.addRow("Status", self.output_status)
        layout.addWidget(box)

        # -- speech recognition ------------------------------------------------------------------------
        box = QGroupBox("Speech recognition (your voice → text)")
        rec = QFormLayout(box)
        self.input_status = QLabel()
        self.input_status.setWordWrap(True)
        rec.addRow("Status", self.input_status)
        self.mic_status = QLabel()
        self.mic_status.setWordWrap(True)
        rec.addRow("Microphone", self.mic_status)
        model_row = QHBoxLayout()
        self.stt_model = QComboBox()
        self.stt_model.setEditable(True)
        for name, note in SPEECH_MODELS:
            self.stt_model.addItem(f"{name}  ({note})", name)
        self.stt_model.activated.connect(lambda _: self._save_stt_model())
        self.stt_model.lineEdit().editingFinished.connect(self._save_stt_model)
        model_row.addWidget(self.stt_model, 1)
        folder = QPushButton("Folder…")
        folder.setToolTip("Use a model folder you downloaded yourself")
        folder.clicked.connect(self._browse_stt)
        model_row.addWidget(folder)
        rec.addRow("Speech model", model_row)
        self.allow_download = self._check("Allow GREEN to download the speech model once (from Hugging Face)",
                                          "allow_model_download")
        rec.addRow("", self.allow_download)
        prepare_row = QHBoxLayout()
        self.prepare_button = QPushButton("Prepare speech model")
        self.prepare_button.setToolTip("Load the model now so the first voice message is quick (downloads it if allowed)")
        self.prepare_button.clicked.connect(self._prepare_model)
        prepare_row.addWidget(self.prepare_button)
        prepare_row.addStretch(1)
        rec.addRow("", prepare_row)
        self.prepare_status = QLabel()
        self.prepare_status.setWordWrap(True)
        rec.addRow("", self.prepare_status)
        layout.addWidget(box)

        note = QLabel(
            "Setup: speech output works with the voices built into Windows. For talking, install the optional "
            "packages once:  pip install -r requirements-voice.txt  (in GREEN's PowerShell window), then click "
            "Prepare speech model. Wake-word listening is not available: GREEN never listens in the background."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

        speech.speaking_changed.connect(lambda on: self.output_status.setText("Speaking…" if on else self._output_text))
        speech.failed.connect(self.output_status.setText)
        self._output_text = ""

    # -- helpers -----------------------------------------------------------------------------------
    def _check(self, text: str, field: str) -> QCheckBox:
        box = QCheckBox(text)
        box.toggled.connect(lambda on, f=field: self._save(f, bool(on)))
        return box

    def _save(self, field: str, value) -> None:
        if self._loading:
            return
        try:
            self.services.config_store.update(lambda c: setattr(c.voice, field, value))
        except Exception as exc:
            self.prepare_status.setText(f"Could not save: {exc}")
            return
        if field in ("tts_engine", "piper_model_path", "system_voice"):
            self._refresh_output()
        if field == "tts_engine":
            self._update_engine_rows()
        self.voice_settings_changed.emit()

    def _save_stt_model(self) -> None:
        index = self.stt_model.currentIndex()
        text = self.stt_model.currentText().strip()
        value = self.stt_model.itemData(index) if index >= 0 and self.stt_model.itemText(index) == text else text
        if value:
            self._save("stt_model", value)

    def _update_engine_rows(self) -> None:
        piper = self.services.config_store.get().voice.tts_engine == "piper"
        for widget in self._piper_widgets:
            widget.setVisible(piper)
        self.voice.setEnabled(not piper)
        self.rate.setEnabled(not piper)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    # -- refresh -----------------------------------------------------------------------------------
    def refresh(self) -> None:
        voice = self.services.config_store.get().voice
        self._loading = True
        try:
            for box, value in ((self.enabled, voice.enabled), (self.conversation_mode, voice.conversation_mode),
                               (self.auto_stop, voice.auto_stop_on_silence), (self.speak_all, voice.speak_responses),
                               (self.muted, voice.muted), (self.allow_download, voice.allow_model_download)):
                box.setChecked(value)
            index = self.silence.findData(voice.silence_seconds)
            if index < 0:
                self.silence.addItem(f"{voice.silence_seconds:g} seconds", voice.silence_seconds)
                index = self.silence.count() - 1
            self.silence.setCurrentIndex(index)
            self.engine.setCurrentIndex(max(0, self.engine.findData(voice.tts_engine)))
            self.rate.setValue(round(voice.speech_rate * 10))
            self.piper_path.setText(voice.piper_model_path)
            self.voice.clear()
            self.voice.addItem("Default system voice", "")
            for name in self.speech.voices():
                self.voice.addItem(name, name)
            self.voice.setCurrentIndex(max(0, self.voice.findData(voice.system_voice)))
            index = self.stt_model.findData(voice.stt_model)
            if index >= 0:
                self.stt_model.setCurrentIndex(index)
            else:
                self.stt_model.setEditText(voice.stt_model)
        finally:
            self._loading = False
        self._update_engine_rows()
        self._refresh_output()
        self._refresh_input()

    def _refresh_output(self) -> None:
        ok, detail = self.speech.check()
        self._output_text = ("Ready — " if ok else "Unavailable — ") + detail
        self.output_status.setText(self._output_text)
        self.test_voice.setEnabled(ok)

    def _refresh_input(self) -> None:
        self.input_status.setText("Checking…")
        self.mic_status.setText("Checking…")

        def work():
            return self.services.voice_input.check_engine(), self.services.voice_input.check_microphone()

        def done(result):
            engine, mic = result
            self.input_status.setText(("Ready — " if engine.available else "Not installed — ") + engine.detail)
            self.mic_status.setText(("Found — " if mic.available else "Not available — ") + mic.detail)
            self.prepare_button.setEnabled(engine.available)

        self.runner.submit(work, on_done=done, on_error=lambda exc: self.input_status.setText(f"Check failed: {exc}"))

    # -- actions -----------------------------------------------------------------------------------
    def _prepare_model(self) -> None:
        voice = self.services.config_store.get().voice
        if not voice.allow_model_download and not Path(voice.stt_model).expanduser().is_dir():
            self.prepare_status.setText(
                "Tick “Allow GREEN to download the speech model once” first, or choose a downloaded model folder."
            )
            return
        self.prepare_button.setEnabled(False)
        self.prepare_status.setText(f"Loading “{voice.stt_model}”… (a first download can take a few minutes)")

        def done(name):
            self.prepare_button.setEnabled(True)
            self.prepare_status.setText(f"Speech model “{name}” is ready. Click Talk in the chat to try it.")

        def failed(exc):
            self.prepare_button.setEnabled(True)
            self.prepare_status.setText(str(exc) if isinstance(exc, VoiceError) else f"Could not load the model: {exc}")

        self.runner.submit(self.services.voice_input.prepare, on_done=done, on_error=failed)

    def _browse_piper(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a Piper voice", "", "Piper voice (*.onnx)")
        if path:
            self.piper_path.setText(path)
            self._save("piper_model_path", path)

    def _browse_stt(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose a faster-whisper model folder")
        if folder:
            self.stt_model.setEditText(folder)
            self._save("stt_model", folder)
