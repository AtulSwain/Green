"""Optional local text-to-speech using Piper voices.

The voice model (.onnx plus its .onnx.json) must be downloaded by the user and
selected in Settings; GREEN never downloads voices itself.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import re
import threading
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import VoiceSettings
from .voice_input_service import ComponentStatus, VoiceError

log = logging.getLogger(__name__)

MAX_SPOKEN_CHARS = 4000


def speakable_text(markdown: str) -> str:
    """Strip code blocks and Markdown syntax so responses read naturally."""
    text = re.sub(r"```.*?```", " (code omitted) ", markdown, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"[*_~>|]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_SPOKEN_CHARS]


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


class VoiceOutputService:
    def __init__(self, settings_provider: Callable[[], VoiceSettings], sd_module: Any = None, voice_loader: Any = None):
        self._settings_provider = settings_provider
        self._sd = sd_module
        self._voice_loader = voice_loader
        self._voice = None
        self._voice_path: str | None = None
        self._stop = threading.Event()
        self._speaking = threading.Event()

    @property
    def is_speaking(self) -> bool:
        return self._speaking.is_set()

    def check(self) -> ComponentStatus:
        if self._voice_loader is None and not _module_available("piper"):
            return ComponentStatus(False, "Piper is not installed (pip install -r requirements-voice.txt).")
        if self._sd is None and not _module_available("sounddevice"):
            return ComponentStatus(False, "The 'sounddevice' package is not installed.")
        path = self._settings_provider().piper_model_path
        if not path:
            return ComponentStatus(False, "No Piper voice selected. Download a voice (.onnx + .onnx.json) and choose it in Settings.")
        model = Path(path).expanduser()
        if not model.is_file():
            return ComponentStatus(False, f"Piper voice file not found: {model}")
        if not Path(str(model) + ".json").is_file():
            return ComponentStatus(False, f"The voice configuration {model.name}.json is missing next to the model.")
        return ComponentStatus(True, f"Piper voice: {model.name}")

    def _load_voice(self):
        path = self._settings_provider().piper_model_path
        status = self.check()
        if not status.available:
            raise VoiceError(status.detail)
        if self._voice is not None and self._voice_path == path:
            return self._voice
        loader = self._voice_loader
        if loader is None:
            from piper import PiperVoice  # noqa: PLC0415

            loader = PiperVoice.load
        try:
            self._voice = loader(str(Path(path).expanduser()))
        except Exception as exc:
            raise VoiceError(f"Could not load the Piper voice: {exc}") from exc
        self._voice_path = path
        return self._voice

    def synthesize(self, text: str):
        """Return (int16 numpy audio, sample_rate)."""
        import numpy as np  # noqa: PLC0415

        voice = self._load_voice()
        chunks = []
        sample_rate = None
        synth = getattr(voice, "synthesize", None)
        try:
            # piper-tts >= 1.3: synthesize(text) yields AudioChunk objects.
            result = synth(text) if synth is not None else None
            if result is not None and not isinstance(result, (bytes, str)):
                for chunk in result:
                    sample_rate = chunk.sample_rate
                    chunks.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
        except TypeError:
            # piper-tts 1.2: synthesize(text, wave_file)
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as wav:
                voice.synthesize(text, wav)
            buffer.seek(0)
            with wave.open(buffer, "rb") as wav:
                sample_rate = wav.getframerate()
                chunks.append(np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16))
        if not chunks or sample_rate is None:
            raise VoiceError("Piper produced no audio.")
        return np.concatenate(chunks), int(sample_rate)

    def speak(self, markdown: str) -> None:
        """Synthesize and play, blocking until finished or :meth:`stop` is called."""
        settings = self._settings_provider()
        if settings.muted:
            return
        text = speakable_text(markdown)
        if not text:
            return
        self._stop.clear()
        audio, rate = self.synthesize(text)
        if self._stop.is_set():
            return
        sd = self._sd
        if sd is None:
            try:
                import sounddevice as sd  # noqa: PLC0415
            except (ImportError, OSError) as exc:
                raise VoiceError(f"Audio output unavailable: {exc}") from exc
            self._sd = sd
        self._speaking.set()
        try:
            sd.play(audio, rate)
            duration = len(audio) / rate
            # Poll so stop() takes effect quickly.
            waited = 0.0
            while waited < duration + 0.5 and not self._stop.wait(0.1):
                waited += 0.1
            sd.stop()
        finally:
            self._speaking.clear()

    def stop(self) -> None:
        self._stop.set()
        if self._sd is not None and self._speaking.is_set():
            try:
                self._sd.stop()
            except Exception:
                pass
