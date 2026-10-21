"""Voice tests use fakes: no microphone, speech model or speakers are needed."""

import importlib.util
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

from green.config import VoiceSettings
from green.services.voice_input_service import SAMPLE_RATE, VoiceError, VoiceInputService
from green.services.voice_output_service import VoiceOutputService, speakable_text


class FakeStream:
    def __init__(self, callback):
        self.callback = callback
        self.started = self.closed = False

    def start(self):
        self.started = True
        block = np.full((SAMPLE_RATE, 1), 0.1, dtype="float32")
        self.callback(block, len(block), None, None)

    def stop(self):
        pass

    def close(self):
        self.closed = True


class FakeSD:
    def __init__(self, has_input=True):
        self.has_input = has_input
        self.played = []
        self.stopped = 0

    def query_devices(self, kind=None):
        if not self.has_input:
            raise RuntimeError("Error querying device -1")
        return {"name": "Test Mic"}

    def InputStream(self, samplerate, channels, dtype, callback):  # noqa: N802
        self.stream = FakeStream(callback)
        return self.stream

    def play(self, audio, rate):
        self.played.append((len(audio), rate))

    def stop(self):
        self.stopped += 1


class FakeWhisper:
    calls = []

    def __init__(self, model, **kwargs):
        FakeWhisper.calls.append((model, kwargs))

    def transcribe(self, audio, language=None, vad_filter=True):
        return [SimpleNamespace(text=" open my notes ")], None


@pytest.mark.skipif(importlib.util.find_spec("faster_whisper") is not None, reason="voice extras installed")
def test_missing_dependencies_reported():
    service = VoiceInputService(lambda: VoiceSettings())
    status = service.check_engine()
    assert not status.available and "faster-whisper" in status.detail


def test_missing_microphone_reported():
    service = VoiceInputService(lambda: VoiceSettings(), sd_module=FakeSD(has_input=False))
    status = service.check_microphone()
    assert not status.available and "No microphone" in status.detail


def test_record_and_transcribe_with_fakes():
    FakeWhisper.calls.clear()
    sd = FakeSD()
    service = VoiceInputService(lambda: VoiceSettings(), sd_module=sd, model_factory=FakeWhisper)
    assert service.check_microphone().available
    service.start_recording()
    assert service.is_recording
    audio = service.stop_recording()
    assert not service.is_recording and sd.stream.closed
    assert audio.shape == (SAMPLE_RATE,)
    assert service.transcribe(audio) == "open my notes"
    assert FakeWhisper.calls[0][1]["local_files_only"] is True  # no silent downloads


def test_short_recording_rejected():
    service = VoiceInputService(lambda: VoiceSettings(), sd_module=FakeSD(), model_factory=FakeWhisper)
    with pytest.raises(VoiceError, match="too short"):
        service.transcribe(np.zeros(100, dtype="float32"))


def test_model_unavailable_locally_is_explained():
    def failing_factory(*args, **kwargs):
        raise RuntimeError("not cached")

    service = VoiceInputService(lambda: VoiceSettings(), sd_module=FakeSD(), model_factory=failing_factory)
    with pytest.raises(VoiceError, match="not available locally"):
        service.transcribe(np.zeros(SAMPLE_RATE, dtype="float32"))


def test_tts_requires_voice_file(tmp_path):
    service = VoiceOutputService(lambda: VoiceSettings(), sd_module=FakeSD(), voice_loader=lambda p: None)
    assert not service.check().available
    missing = VoiceOutputService(lambda: VoiceSettings(piper_model_path=str(tmp_path / "x.onnx")), sd_module=FakeSD(), voice_loader=lambda p: None)
    assert "not found" in missing.check().detail


def test_tts_speaks_and_respects_mute(tmp_path):
    model = tmp_path / "voice.onnx"
    model.write_bytes(b"x")
    (tmp_path / "voice.onnx.json").write_text("{}")

    class FakeVoice:
        def synthesize(self, text):
            yield SimpleNamespace(sample_rate=22050, audio_int16_bytes=np.zeros(2205, dtype=np.int16).tobytes())

    sd = FakeSD()
    settings = VoiceSettings(piper_model_path=str(model))
    service = VoiceOutputService(lambda: settings, sd_module=sd, voice_loader=lambda p: FakeVoice())
    assert service.check().available
    service.speak("**Hello** `world`")
    assert sd.played == [(2205, 22050)]
    muted = VoiceOutputService(lambda: VoiceSettings(piper_model_path=str(model), muted=True), sd_module=sd, voice_loader=lambda p: FakeVoice())
    muted.speak("ignored")
    assert len(sd.played) == 1


def test_speakable_text():
    text = "# Title\nUse `pip`:\n```bash\npip install x\n```\nSee [docs](https://x.y)."
    assert speakable_text(text) == "Title Use pip: (code omitted) See docs."
