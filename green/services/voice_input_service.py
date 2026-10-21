"""Optional local speech-to-text using faster-whisper and sounddevice.

Everything here is optional: when the packages, a microphone or the model are
missing, :meth:`VoiceInputService.check` explains why and the rest of GREEN keeps
working. Audio is never sent anywhere; recording only happens after the user clicks
the microphone button, and stops on a second click, after silence, or at the time limit.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import VoiceSettings

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
SPEECH_RMS = 0.02  # RMS loudness treated as speech (float samples in -1..1)


class VoiceError(Exception):
    """User-presentable voice error."""


@dataclass
class ComponentStatus:
    available: bool
    detail: str


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


class VoiceInputService:
    def __init__(self, settings_provider: Callable[[], VoiceSettings], sd_module: Any = None, model_factory: Any = None):
        self._settings_provider = settings_provider
        self._sd = sd_module
        self._model_factory = model_factory
        self._model = None
        self._model_key: tuple | None = None
        self._lock = threading.Lock()
        self._stream = None
        self._frames: list[Any] = []
        self._frame_count = 0
        self._max_frames = 0
        # Live input analysis, written by the audio thread and read by the GUI.
        self.level = 0.0  # 0..1 for a level meter
        self._speech_seen = False
        self._silent_frames = 0

    # -- dependency checks ----------------------------------------------------------
    def _sounddevice(self):
        if self._sd is None:
            if not _module_available("sounddevice"):
                raise VoiceError("The 'sounddevice' package is not installed (pip install -r requirements-voice.txt).")
            try:
                import sounddevice  # noqa: PLC0415
            except OSError as exc:  # PortAudio library missing
                raise VoiceError(f"Audio system unavailable: {exc}") from exc
            self._sd = sounddevice
        return self._sd

    def check_engine(self) -> ComponentStatus:
        if self._model_factory is None and not _module_available("faster_whisper"):
            return ComponentStatus(False, "faster-whisper is not installed (pip install -r requirements-voice.txt).")
        settings = self._settings_provider()
        model = settings.stt_model
        if Path(model).expanduser().is_dir():
            return ComponentStatus(True, f"faster-whisper ready (local model folder {model}).")
        if settings.allow_model_download:
            return ComponentStatus(True, f"faster-whisper ready; model '{model}' will be downloaded on first use if not cached.")
        return ComponentStatus(
            True,
            f"faster-whisper installed; model '{model}' must already be cached or be a local folder "
            "(tick 'Allow … download' on the Voice screen).",
        )

    def check_microphone(self) -> ComponentStatus:
        try:
            sd = self._sounddevice()
            device = sd.query_devices(kind="input")
        except VoiceError as exc:
            return ComponentStatus(False, str(exc))
        except Exception as exc:  # PortAudioError and friends
            return ComponentStatus(False, f"No microphone available ({exc.__class__.__name__}).")
        name = device.get("name", "default") if isinstance(device, dict) else "default"
        return ComponentStatus(True, f"Input device: {name}")

    # -- recording ------------------------------------------------------------------
    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    def start_recording(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            sd = self._sounddevice()
            max_frames = SAMPLE_RATE * self._settings_provider().max_recording_seconds
            self._max_frames = max_frames
            self._frames = []
            self._frame_count = 0
            self.level = 0.0
            self._speech_seen = False
            self._silent_frames = 0

            def callback(indata, frames, time_info, status):  # runs on the audio thread
                if self._frame_count < max_frames:
                    self._frames.append(indata.copy())
                    self._frame_count += frames
                    self._analyse(indata, frames)

            try:
                stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback)
                stream.start()
            except Exception as exc:
                raise VoiceError(f"Could not start the microphone: {exc}") from exc
            self._stream = stream
            log.info("Push-to-talk recording started")

    def _analyse(self, block, frames: int) -> None:
        """Track loudness so the recording can stop by itself after the speaker goes quiet."""
        try:
            rms = float((block.astype("float32") ** 2).mean() ** 0.5)
        except Exception:
            return
        self.level = min(1.0, rms * 8)
        if rms >= SPEECH_RMS:
            self._speech_seen = True
            self._silent_frames = 0
        else:
            self._silent_frames += frames

    def should_auto_stop(self) -> bool:
        """True once speech was heard and then silence lasted long enough, or the time limit was hit."""
        if self._stream is None:
            return False
        if self._max_frames and self._frame_count >= self._max_frames:
            return True
        settings = self._settings_provider()
        if not settings.auto_stop_on_silence or not self._speech_seen:
            return False
        return self._silent_frames >= int(settings.silence_seconds * SAMPLE_RATE)

    def stop_recording(self):
        """Stop recording and return mono float32 audio (numpy array)."""
        with self._lock:
            stream, self._stream = self._stream, None
            frames, self._frames = self._frames, []
            self.level = 0.0
        if stream is None:
            raise VoiceError("Not recording.")
        try:
            stream.stop()
            stream.close()
        except Exception:
            log.warning("Error while closing the microphone stream")
        log.info("Push-to-talk recording stopped")
        import numpy as np  # noqa: PLC0415 - numpy ships with the voice extras

        if not frames:
            return np.zeros(0, dtype="float32")
        return np.concatenate(frames, axis=0).reshape(-1).astype("float32")

    # -- transcription ----------------------------------------------------------------
    def _load_model(self):
        settings = self._settings_provider()
        key = (settings.stt_model, settings.stt_device, settings.stt_compute_type, settings.allow_model_download)
        if self._model is not None and self._model_key == key:
            return self._model
        factory = self._model_factory
        if factory is None:
            try:
                from faster_whisper import WhisperModel  # noqa: PLC0415
            except ImportError as exc:
                raise VoiceError("faster-whisper is not installed.") from exc
            factory = WhisperModel
        try:
            self._model = factory(
                settings.stt_model,
                device=settings.stt_device,
                compute_type=settings.stt_compute_type,
                local_files_only=not settings.allow_model_download,
            )
        except Exception as exc:
            if not settings.allow_model_download:
                raise VoiceError(
                    f"The speech model '{settings.stt_model}' is not available locally. On the Voice screen, tick "
                    "'Allow GREEN to download the speech model once' and click Prepare speech model, or choose a "
                    "downloaded model folder."
                ) from exc
            raise VoiceError(f"Could not load the speech model: {exc}") from exc
        self._model_key = key
        return self._model

    def prepare(self) -> str:
        """Load the speech model now (downloading it first only if the user allowed that)."""
        self._load_model()
        return self._settings_provider().stt_model

    def transcribe(self, audio) -> str:
        if audio is None or len(audio) < SAMPLE_RATE * 0.3:
            raise VoiceError("The recording was too short. Hold the microphone button while speaking.")
        model = self._load_model()
        language = self._settings_provider().stt_language or None
        segments, _info = model.transcribe(audio, language=language, vad_filter=True)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text:
            raise VoiceError("No speech was recognized.")
        return text
