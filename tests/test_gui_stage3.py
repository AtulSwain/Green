"""Stage 3 GUI: voice conversation, speech output, quick actions, code blocks, model switcher,
regenerate, export, sidebar search and speed stats."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from green.ui.main_window import MainWindow
from green.ui.speech import SpeechOutput

from .fake_ollama import text_chunks
from .test_gui import message_widgets, send

np = pytest.importorskip("numpy")


@pytest.fixture
def window(qtbot, services):
    win = MainWindow(services, start_timers=False)
    qtbot.addWidget(win)
    win.show()
    yield win
    win.shutdown()


# -- speech output --------------------------------------------------------------------------------

def test_system_speech_with_mock_engine(qtbot, services):
    from green.ui.workers import TaskRunner

    runner = TaskRunner()
    speech = SpeechOutput(services, runner, engine="mock")
    ok, detail = speech.check()
    assert ok and speech.voices(), detail
    with qtbot.waitSignal(speech.speaking_changed, timeout=3000) as started:
        assert speech.speak("**Hello** there")
    assert started.args == [True]
    speech.stop()
    assert not speech.is_speaking
    services.config_store.update(lambda c: setattr(c.voice, "muted", True))
    assert speech.speak("muted") is False
    assert speech.speak("explicit read aloud", respect_mute=False) is True
    speech.stop()
    runner.shutdown()


def test_read_aloud_button(qtbot, window, fake_ollama):
    fake_ollama.chat_script = [text_chunks("The answer is 42.")]
    spoken = []
    window.speech.speak = lambda text, respect_mute=True: spoken.append((text, respect_mute)) or True
    send(qtbot, window, "question")
    reply = message_widgets(window)[-1]
    assert reply.speak_button.isVisible() and not message_widgets(window)[0].speak_button.isVisible()
    qtbot.mouseClick(reply.speak_button, Qt.MouseButton.LeftButton)
    assert spoken == [("The answer is 42.", False)]


def test_typed_messages_are_not_spoken_by_default(qtbot, window, fake_ollama):
    spoken = []
    window.speech.speak = lambda text, respect_mute=True: spoken.append(text) or True
    send(qtbot, window, "hello")
    assert spoken == []


# -- voice conversation ----------------------------------------------------------------------------

class FakeStream:
    def __init__(self, callback):
        self.callback = callback

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


class FakeSD:
    def InputStream(self, samplerate, channels, dtype, callback):  # noqa: N802
        self.stream = FakeStream(callback)
        return self.stream

    def query_devices(self, kind=None):
        return {"name": "Fake mic"}


class FakeWhisper:
    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, audio, language=None, vad_filter=True):
        return [SimpleNamespace(text=" What's the time? ")], None


def feed(sd, level, seconds=0.25):
    frames = int(16_000 * seconds)
    sd.stream.callback(np.full((frames, 1), level, dtype="float32"), frames, None, None)


def test_click_to_talk_conversation(qtbot, window, services, fake_ollama):
    sd = FakeSD()
    services.voice_input._sd = sd
    services.voice_input._model_factory = FakeWhisper
    services.config_store.update(lambda c: (setattr(c.voice, "enabled", True), setattr(c.voice, "silence_seconds", 0.5)))
    window.chat_view.apply_config()
    spoken = []
    window.speech.speak = lambda text, respect_mute=True: spoken.append(text) or True
    fake_ollama.chat_script = [text_chunks("It is 3 pm.")]
    view = window.chat_view
    assert view.mic_button.isVisible()

    qtbot.mouseClick(view.mic_button, Qt.MouseButton.LeftButton)
    assert services.voice_input.is_recording and view.level_bar.isVisible()
    assert "Listening" in view.mic_button.text()
    feed(sd, 0.3)
    qtbot.waitUntil(lambda: view.level_bar.value() > 50, timeout=2000)
    for _ in range(3):
        feed(sd, 0.0)  # the user stops talking → recording stops by itself
    with qtbot.waitSignal(window.controller.generation_finished, timeout=10000):
        pass
    assert not services.voice_input.is_recording and not view.level_bar.isVisible()
    user, reply = message_widgets(window)
    assert user.raw == "What's the time?" and reply.raw == "It is 3 pm."
    assert spoken == ["It is 3 pm."]  # answered out loud because the question was spoken


def test_click_again_stops_recording(qtbot, window, services, fake_ollama):
    sd = FakeSD()
    services.voice_input._sd = sd
    services.voice_input._model_factory = FakeWhisper
    services.config_store.update(lambda c: (setattr(c.voice, "enabled", True),
                                            setattr(c.voice, "conversation_mode", False)))
    window.chat_view.apply_config()
    view = window.chat_view
    view.toggle_recording()
    feed(sd, 0.3, seconds=0.5)
    view.toggle_recording()
    qtbot.waitUntil(lambda: view.input.toPlainText() == "What's the time?", timeout=5000)
    assert not window.controller.is_busy  # without conversation mode the transcript waits for review


def test_microphone_problem_is_explained(qtbot, window, services):
    services.config_store.update(lambda c: setattr(c.voice, "enabled", True))
    window.chat_view.apply_config()

    def broken():
        from green.services.voice_input_service import VoiceError
        raise VoiceError("No microphone available.")

    services.voice_input._sounddevice = broken
    window.chat_view.toggle_recording()
    assert window.chat_view.banner.isVisible() and "No microphone" in window.chat_view.banner.label.text()


# -- chat improvements ------------------------------------------------------------------------------

def test_quick_actions_fill_composer(qtbot, window):
    view = window.chat_view
    assert len(view.quick_buttons) == 6
    qtbot.mouseClick(view.quick_buttons[4], Qt.MouseButton.LeftButton)
    assert view.input.toPlainText().startswith("What is 18%") and view.input.hasFocus()


def test_code_blocks_render_with_copy(qtbot, window, fake_ollama):
    fake_ollama.chat_script = [text_chunks("Try this:\n\n```python\nprint('hi')\n```\nDone.")]
    send(qtbot, window, "code please")
    blocks = message_widgets(window)[-1].body.code_blocks()
    assert len(blocks) == 1 and blocks[0].language.text() == "python"
    qtbot.mouseClick(blocks[0].copy_button, Qt.MouseButton.LeftButton)
    assert QGuiApplication.clipboard().text() == "print('hi')"


def test_speed_stats_shown(qtbot, window, fake_ollama):
    send(qtbot, window, "hi")
    meta = message_widgets(window)[-1].meta.text()
    assert "qwen3:8b" in meta and "25.0 tok/s" in meta and "model load 1.5 s" in meta


def test_model_switcher(qtbot, window, services, fake_ollama):
    fake_ollama.models = ["qwen3:8b", "qwen3:4b"]
    window.check_status()
    combo = window.chat_view.model_combo
    qtbot.waitUntil(lambda: combo.count() == 2, timeout=5000)
    with qtbot.waitSignal(window.chat_view.model_switched, timeout=2000):
        combo.setCurrentIndex(combo.findText("qwen3:4b"))
        combo.activated.emit(combo.currentIndex())
    assert services.config_store.get().ollama.model == "qwen3:4b"
    send(qtbot, window, "hi")
    chats = [r["body"] for r in fake_ollama.requests if r["path"] == "/api/chat"]
    assert chats[-1]["model"] == "qwen3:4b"


def test_regenerate_last_reply(qtbot, window, services, fake_ollama):
    fake_ollama.chat_script = [text_chunks("first answer"), text_chunks("second answer")]
    send(qtbot, window, "question")
    reply = message_widgets(window)[-1]
    assert reply.regenerate_button.isVisible() and not message_widgets(window)[0].regenerate_button.isVisible()
    with qtbot.waitSignal(window.controller.generation_finished, timeout=10000):
        qtbot.mouseClick(reply.regenerate_button, Qt.MouseButton.LeftButton)
    qtbot.wait(50)
    assert [w.raw for w in message_widgets(window)] == ["question", "second answer"]
    stored = services.conversations.messages(window.chat_view.conversation_id)
    assert [m.content for m in stored] == ["question", "second answer"]


def test_export_conversation(qtbot, window, tmp_path, monkeypatch):
    from green.ui import chat_view as chat_view_module

    send(qtbot, window, "export me")
    target = tmp_path / "chat.md"
    monkeypatch.setattr(chat_view_module.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    qtbot.mouseClick(window.chat_view.export_button, Qt.MouseButton.LeftButton)
    text = target.read_text(encoding="utf-8")
    assert "export me" in text and "Hello from the fake model." in text


def test_sidebar_search(qtbot, window, services):
    a = services.conversations.create("Holiday plans")
    b = services.conversations.create("Work notes")
    services.conversations.add_message(b.id, "user", "budget spreadsheet")
    window.sidebar.reload()
    assert window.sidebar.list.count() == 2
    window.sidebar.search.setText("budget")
    qtbot.waitUntil(lambda: window.sidebar.list.count() == 1, timeout=2000)
    assert window.sidebar.list.item(0).text() == "Work notes"
    window.sidebar.search.setText("nothing-matches")
    qtbot.waitUntil(lambda: window.sidebar.list.count() == 0, timeout=2000)
    assert window.sidebar.empty_label.text() == "No matching conversations."
    assert a.id


def test_voice_screen_controls_save(qtbot, window, services):
    window.show_page("voice")
    view = window.voice_view
    view.enabled.setChecked(True)
    view.speak_all.setChecked(True)
    view.rate.setValue(5)
    voice = services.config_store.get().voice
    assert voice.enabled and voice.speak_responses and voice.speech_rate == 0.5
    assert not window.chat_view.mic_button.isHidden()
    assert view.test_voice.isEnabled()  # mock engine has voices


def test_window_has_icon(window):
    assert not window.windowIcon().isNull()
