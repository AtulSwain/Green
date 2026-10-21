"""Speech output for the GUI.

The default engine is Qt's text-to-speech, which on Windows uses the voices built into
the operating system (no download). A Piper voice file can be used instead. Speaking is
asynchronous and never blocks the window.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, Signal

from ..app.bootstrap import AppServices
from ..services.voice_input_service import VoiceError
from ..services.voice_output_service import speakable_text
from .workers import TaskRunner

log = logging.getLogger(__name__)

try:  # part of PySide6-Addons, installed with PySide6
    from PySide6.QtTextToSpeech import QTextToSpeech
except ImportError:  # pragma: no cover - depends on the installation
    QTextToSpeech = None


class SpeechOutput(QObject):
    speaking_changed = Signal(bool)
    failed = Signal(str)

    def __init__(self, services: AppServices, runner: TaskRunner, engine: str | None = None, parent=None):
        super().__init__(parent)
        self.services = services
        self.runner = runner
        # GREEN_TTS_ENGINE lets tests select Qt's "mock" engine.
        self._engine_name = engine or os.environ.get("GREEN_TTS_ENGINE") or None
        self._tts = None
        self._tts_failed = False
        self._piper_busy = False
        self._speaking = False

    # -- engine -------------------------------------------------------------------------------
    def _system(self):
        if self._tts is None and not self._tts_failed:
            if QTextToSpeech is None:
                self._tts_failed = True
                return None
            try:
                self._tts = QTextToSpeech(self._engine_name, self) if self._engine_name else QTextToSpeech(self)
                self._tts.stateChanged.connect(self._on_state)
                if hasattr(self._tts, "errorOccurred"):
                    self._tts.errorOccurred.connect(lambda *_: self._on_error())
            except Exception:
                log.exception("Text-to-speech could not be initialised")
                self._tts_failed = True
                self._tts = None
        return self._tts

    def _use_piper(self) -> bool:
        return self.services.config_store.get().voice.tts_engine == "piper"

    def check(self) -> tuple[bool, str]:
        if self._use_piper():
            status = self.services.voice_output.check()
            return status.available, status.detail
        tts = self._system()
        if tts is None:
            return False, "Text-to-speech is not available in this PySide6 installation."
        if tts.state() == QTextToSpeech.State.Error:
            return False, f"The system speech engine reported an error: {tts.errorString() or 'unknown'}"
        voices = tts.availableVoices()
        if not voices:
            return False, "No system voices are installed. On Windows: Settings → Time & language → Speech."
        return True, f"System voice: {tts.voice().name() or voices[0].name()} ({len(voices)} voice(s) installed)"

    def voices(self) -> list[str]:
        tts = self._system()
        if tts is None:
            return []
        return [voice.name() for voice in tts.availableVoices()]

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    # -- speaking -------------------------------------------------------------------------------
    def speak(self, markdown: str, *, respect_mute: bool = True) -> bool:
        """Read text aloud. Returns False if nothing was spoken."""
        voice = self.services.config_store.get().voice
        if respect_mute and voice.muted:
            return False
        text = speakable_text(markdown)
        if not text:
            return False
        self.stop()
        if self._use_piper():
            return self._speak_piper(text)
        tts = self._system()
        if tts is None or tts.state() == QTextToSpeech.State.Error:
            self.failed.emit(self.check()[1])
            return False
        for candidate in tts.availableVoices():
            if voice.system_voice and candidate.name() == voice.system_voice:
                tts.setVoice(candidate)
                break
        tts.setRate(voice.speech_rate)
        tts.say(text)
        self._set_speaking(True)
        return True

    def _speak_piper(self, text: str) -> bool:
        self._piper_busy = True
        self._set_speaking(True)

        def done(_=None):
            self._piper_busy = False
            self._set_speaking(False)

        def failed(error):
            done()
            self.failed.emit(str(error) if isinstance(error, VoiceError) else "Speech output failed.")

        self.runner.submit(lambda: self.services.voice_output.speak(text, respect_mute=False), on_done=done, on_error=failed)
        return True

    def stop(self) -> None:
        if self._tts is not None and self._speaking:
            self._tts.stop()
        if self._piper_busy:
            self.services.voice_output.stop()
        self._set_speaking(False)

    def _on_state(self, state) -> None:
        speaking = state in (QTextToSpeech.State.Speaking, getattr(QTextToSpeech.State, "Synthesizing", None))
        self._set_speaking(bool(speaking))
        if state == QTextToSpeech.State.Error:
            self._on_error()

    def _on_error(self) -> None:
        self._set_speaking(False)
        message = self._tts.errorString() if self._tts is not None else ""
        self.failed.emit(f"Speech output failed{': ' + message if message else '.'}")

    def _set_speaking(self, speaking: bool) -> None:
        if speaking != self._speaking:
            self._speaking = speaking
            self.speaking_changed.emit(speaking)
