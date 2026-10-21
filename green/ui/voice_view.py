"""Voice screen: component status, mute, spoken replies and a speech-output test."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..app.bootstrap import AppServices
from ..services.voice_input_service import VoiceError
from .workers import TaskRunner


class VoiceView(QWidget):
    def __init__(self, services: AppServices, runner: TaskRunner, parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.services = services
        self.runner = runner
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)
        title = QLabel("Voice")
        title.setObjectName("PageTitle")
        layout.addWidget(title)
        intro = QLabel(
            "Voice runs entirely on this computer. The microphone is only used while you hold the "
            "“Hold to talk” button in the chat; audio is never stored or uploaded."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        status_box = QGroupBox("Status")
        form = QFormLayout(status_box)
        self.stt_label = QLabel("…")
        self.mic_label = QLabel("…")
        self.tts_label = QLabel("…")
        for label in (self.stt_label, self.mic_label, self.tts_label):
            label.setWordWrap(True)
        form.addRow("Speech recognition", self.stt_label)
        form.addRow("Microphone", self.mic_label)
        form.addRow("Speech output", self.tts_label)
        form.addRow("Wake word", QLabel("Not available in this version (no background listening)."))
        refresh = QPushButton("Check again")
        refresh.clicked.connect(self.refresh)
        form.addRow("", refresh)
        layout.addWidget(status_box)

        controls = QGroupBox("Controls")
        controls_layout = QVBoxLayout(controls)
        self.mute = QCheckBox("Mute spoken replies")
        self.mute.toggled.connect(lambda on: self._save("muted", on))
        self.speak = QCheckBox("Read replies aloud")
        self.speak.toggled.connect(lambda on: self._save("speak_responses", on))
        controls_layout.addWidget(self.mute)
        controls_layout.addWidget(self.speak)
        row = QHBoxLayout()
        self.test_button = QPushButton("Test speech output")
        self.test_button.clicked.connect(self._test)
        self.stop_button = QPushButton("Stop speaking")
        self.stop_button.clicked.connect(self.services.voice_output.stop)
        row.addWidget(self.test_button)
        row.addWidget(self.stop_button)
        row.addStretch(1)
        controls_layout.addLayout(row)
        self.message = QLabel()
        self.message.setWordWrap(True)
        controls_layout.addWidget(self.message)
        layout.addWidget(controls)

        setup = QLabel(
            "Setup: install the optional packages with  pip install -r requirements-voice.txt , download a Piper "
            "voice (.onnx and .onnx.json) and select it under Settings → Voice. See README for details."
        )
        setup.setObjectName("Muted")
        setup.setWordWrap(True)
        layout.addWidget(setup)
        layout.addStretch(1)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    def refresh(self) -> None:
        voice = self.services.config_store.get().voice
        for box, value in ((self.mute, voice.muted), (self.speak, voice.speak_responses)):
            box.blockSignals(True)
            box.setChecked(value)
            box.blockSignals(False)
        for label in (self.stt_label, self.mic_label, self.tts_label):
            label.setText("Checking…")

        def work():
            return (
                self.services.voice_input.check_engine(),
                self.services.voice_input.check_microphone(),
                self.services.voice_output.check(),
            )

        def done(result):
            for label, status in zip((self.stt_label, self.mic_label, self.tts_label), result):
                label.setText(("Available — " if status.available else "Unavailable — ") + status.detail)
            self.test_button.setEnabled(result[2].available)

        self.runner.submit(work, on_done=done, on_error=lambda exc: self.message.setText(f"Voice check failed: {exc}"))

    def _save(self, field: str, value: bool) -> None:
        self.services.config_store.update(lambda c: setattr(c.voice, field, value))

    def _test(self) -> None:
        self.test_button.setEnabled(False)
        self.message.setText("Speaking…")

        def done(_):
            self.test_button.setEnabled(True)
            self.message.setText("Speech output finished.")

        def failed(exc):
            self.test_button.setEnabled(True)
            self.message.setText(str(exc) if isinstance(exc, VoiceError) else f"Speech output failed: {exc.__class__.__name__}")

        if self.services.config_store.get().voice.muted:
            self.test_button.setEnabled(True)
            self.message.setText("Spoken replies are muted. Untick “Mute” to test.")
            return
        self.runner.submit(lambda: self.services.voice_output.speak("GREEN voice output is working."), on_done=done, on_error=failed)
