"""Stage 3: calculator, reminders 'in N minutes', conversation search/export/regenerate,
speed statistics, voice auto-stop and message rendering."""

from datetime import datetime, timedelta

import pytest

from green.services.ollama_service import CancelToken
from green.services.tool_registry import ErrorCode
from green.tools.calculator_tool import calculate
from green.ui.message_body import parse_segments

from .fake_ollama import text_chunks


# -- calculator -----------------------------------------------------------------------------------

@pytest.mark.parametrize("expression, expected", [
    ("2 + 3 * 4", 14), ("(1250 * 0.18) + 40", 265), ("2^10", 1024), ("sqrt(16) + abs(-2)", 6),
    ("10 / 4", 2.5), ("7 // 2", 3), ("round(pi, 2)", 3.14), ("max(3, 9, 4)", 9), ("-5 % 3", 1),
    ("factorial(5)", 120), ("12 × 3 ÷ 4", 9),
])
def test_calculator_results(expression, expected):
    assert calculate(expression) == expected


@pytest.mark.parametrize("expression", [
    "__import__('os').system('calc')", "open('x')", "(1).__class__", "[1, 2]", "lambda: 1", "x + 1",
    "2 ** 100000", "9" * 400, "1 / 0", "factorial(5000)", "'a' * 3", "True + 1", "sqrt(-1)", "1 if 1 else 2",
])
def test_calculator_rejects_unsafe_or_invalid(expression):
    from green.services.tool_registry import ToolError

    with pytest.raises(ToolError):
        calculate(expression)


def test_calculator_tool_enabled_by_default(services):
    result = services.registry.invoke("calculate", {"expression": "18/100*2450 + 75"})
    assert result.ok and result.data["result"] == 516 and result.summary == "= 516"
    bad = services.registry.invoke("calculate", {"expression": "__import__('os')"})
    assert bad.code == ErrorCode.POLICY_VIOLATION


# -- reminders ------------------------------------------------------------------------------------

def test_reminder_in_minutes(services):
    before = datetime.now().astimezone()
    result = services.registry.invoke("create_reminder", {"title": "Stretch", "in_minutes": 10}, confirm=lambda r: True)
    assert result.ok
    due = services.reminders.list()[0].due_at.astimezone()
    assert before + timedelta(minutes=9) <= due <= before + timedelta(minutes=11)


def test_reminder_needs_exactly_one_time(services):
    both = services.registry.invoke("create_reminder", {"title": "x", "due": "2030-01-01T09:00", "in_minutes": 5},
                                    confirm=lambda r: True)
    neither = services.registry.invoke("create_reminder", {"title": "x"}, confirm=lambda r: True)
    assert both.code == neither.code == ErrorCode.POLICY_VIOLATION and services.reminders.list() == []


# -- conversations --------------------------------------------------------------------------------

def test_search_titles_and_messages(services):
    conv = services.conversations
    a = conv.create("Holiday plans")
    b = conv.create("Work")
    conv.add_message(b.id, "user", "Fix the PostgreSQL migration")
    conv.create("100% done_list")
    assert [c.id for c in conv.search("holiday")] == [a.id]
    assert [c.id for c in conv.search("postgresql")] == [b.id]
    assert [c.title for c in conv.search("100%")] == ["100% done_list"]
    assert conv.search("_") and conv.search("%") and len(conv.search("")) == 3
    assert conv.search("zzz") == []


def test_regenerate_support(services):
    conv = services.conversations
    c = conv.create()
    conv.add_message(c.id, "user", "first")
    conv.add_message(c.id, "assistant", "reply 1")
    last = conv.add_message(c.id, "user", "second")
    conv.add_message(c.id, "assistant", "reply 2")
    assert conv.last_user_message(c.id).id == last.id
    assert conv.delete_messages_after(c.id, last.id) == 1
    assert [m.content for m in conv.messages(c.id)] == ["first", "reply 1", "second"]


def test_export_markdown(services):
    conv = services.conversations
    c = conv.create("Trip")
    conv.add_message(c.id, "user", "Plan a trip")
    conv.add_message(c.id, "assistant", "Here is a **plan**.")
    text = conv.export_markdown(c.id)
    assert text.startswith("# Trip") and "## You ·" in text and "## GREEN ·" in text and "Here is a **plan**." in text


# -- speed statistics -----------------------------------------------------------------------------

def test_generation_reports_measured_speed(services, fake_ollama):
    fake_ollama.chat_script = [text_chunks("Quick answer.")]
    conv = services.conversations.create()
    services.conversations.add_message(conv.id, "user", "hi")
    result = services.engine.generate(conv.id, CancelToken())
    assert result.stats["tokens"] == 50 and result.stats["tokens_per_second"] == 25.0
    assert result.stats["load_seconds"] == 1.5 and result.stats["model"] == "qwen3:8b"
    assert services.conversations.messages(conv.id)[-1].metadata["stats"]["tokens_per_second"] == 25.0


def test_no_speed_claimed_without_data(services, fake_ollama):
    fake_ollama.chat_script = [[{"message": {"content": "x"}, "done": True}]]
    conv = services.conversations.create()
    services.conversations.add_message(conv.id, "user", "hi")
    stats = services.engine.generate(conv.id, CancelToken()).stats
    assert "tokens_per_second" not in stats and "seconds" in stats


def test_keep_alive_default_keeps_model_loaded():
    from green.config import OllamaSettings

    assert OllamaSettings().keep_alive == "30m"


# -- voice auto-stop ------------------------------------------------------------------------------

@pytest.fixture
def np():
    return pytest.importorskip("numpy")


class _Stream:
    def __init__(self, callback):
        self.callback = callback

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class _SD:
    def InputStream(self, samplerate, channels, dtype, callback):  # noqa: N802
        self.stream = _Stream(callback)
        return self.stream

    def query_devices(self, kind=None):
        return {"name": "Fake mic"}


def _block(np, level: float, seconds: float = 0.25):
    frames = int(16_000 * seconds)
    return np.full((frames, 1), level, dtype="float32"), frames


def _voice(**settings):
    from green.config import VoiceSettings
    from green.services.voice_input_service import VoiceInputService

    sd = _SD()
    voice_settings = VoiceSettings(**settings)
    return VoiceInputService(lambda: voice_settings, sd_module=sd), sd


def test_auto_stop_after_speech_then_silence(np):
    service, sd = _voice(silence_seconds=1.0)
    service.start_recording()
    feed = sd.stream.callback
    for _ in range(4):
        feed(*_block(np, 0.0), None, None)
    assert not service.should_auto_stop()  # silence before speaking does not count
    feed(*_block(np, 0.3), None, None)
    assert service.level > 0.5
    for _ in range(3):
        feed(*_block(np, 0.001), None, None)
    assert not service.should_auto_stop()
    feed(*_block(np, 0.001), None, None)
    assert service.should_auto_stop()
    audio = service.stop_recording()
    assert len(audio) == 16_000 * 0.25 * 9 and service.level == 0.0 and not service.should_auto_stop()


def test_auto_stop_can_be_disabled_but_time_limit_applies(np):
    service, sd = _voice(auto_stop_on_silence=False, max_recording_seconds=1)
    service.start_recording()
    sd.stream.callback(*_block(np, 0.3), None, None)
    for _ in range(2):
        sd.stream.callback(*_block(np, 0.0), None, None)
    assert not service.should_auto_stop()
    sd.stream.callback(*_block(np, 0.0), None, None)
    assert service.should_auto_stop()  # reached max_recording_seconds


# -- message rendering ----------------------------------------------------------------------------

def test_parse_segments():
    text = "Intro\n\n```python\nprint('hi')\n```\nAfter"
    assert [(s.kind, s.content, s.language) for s in parse_segments(text)] == [
        ("text", "Intro", ""), ("code", "print('hi')", "python"), ("text", "After", "")]
    streaming = parse_segments("Look:\n```js\nconst a = 1")
    assert streaming[-1].kind == "code" and streaming[-1].content == "const a = 1"
    assert parse_segments("") == []
