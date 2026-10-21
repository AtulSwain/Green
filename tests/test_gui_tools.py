"""Stage 2 GUI: tool progress, non-blocking approvals, Stop, responsiveness and tool history."""

import time

import pytest
from PySide6.QtCore import Qt, QTimer

from green.services.tool_registry import Tool, ToolCategory
from green.ui.main_window import MainWindow

from .fake_ollama import text_chunks, tool_call_chunks
from .test_gui import message_widgets


class SlowTool(Tool):
    name = "slow_lookup"
    description = "Test tool that takes a while."
    category = ToolCategory.READ

    def describe(self, args, prepared):
        return "Looking something up slowly"

    def run(self, args, prepared):
        time.sleep(1.0)
        return {"answer": 42}

    def summarize(self, data):
        return "Found the answer"


@pytest.fixture
def setup(qtbot, services, fake_ollama, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    services.registry.register(SlowTool())
    services.config_store.update(lambda c: (
        c.tools.enabled_tools.update({"create_text_file": True, "slow_lookup": True}),
        setattr(c.tools, "output_directory", str(out)),
    ))
    win = MainWindow(services, start_timers=False)
    qtbot.addWidget(win)
    win.show()
    yield win, services, fake_ollama, out
    win.shutdown()


def start(qtbot, win, text):
    win.chat_view.input.setPlainText(text)
    qtbot.mouseClick(win.chat_view.send_button, Qt.MouseButton.LeftButton)


def step_texts(win):
    widget = win.chat_view._stream_widget or message_widgets(win)[-1]
    return [(label.property("state"), label.text()) for label in widget._steps.values()]


def ticker():
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(20)
    return timer, ticks


def test_approval_dialog_does_not_block_and_approve_runs(qtbot, setup):
    win, services, fake, out = setup
    fake.chat_script = [tool_call_chunks("create_text_file", {"filename": "notes.txt", "content": "hi\n"}),
                        text_chunks("Created notes.txt.")]
    timer, ticks = ticker()
    start(qtbot, win, "create notes.txt")
    qtbot.waitUntil(lambda: bool(win._active_dialogs), timeout=5000)
    dialog = next(iter(win._active_dialogs))
    assert not dialog.isModal()
    assert win.chat_view.stop_button.isVisible() and win.chat_view.stop_button.isEnabled()
    assert "Waiting for your approval" in win.chat_view.status_label.text()
    assert step_texts(win)[0][0] == "waiting"
    before = len(ticks)
    qtbot.wait(300)
    assert len(ticks) - before >= 8  # event loop keeps running while the dialog is open
    assert not (out / "notes.txt").exists()  # nothing happens before approval
    with qtbot.waitSignal(win.controller.generation_finished, timeout=10000):
        qtbot.mouseClick(dialog.approve_button, Qt.MouseButton.LeftButton)
    timer.stop()
    assert (out / "notes.txt").read_text() == "hi\n"
    state, text = step_texts(win)[0]
    assert state == "ok" and text.startswith("✓  Creating file") and "Created" in text


def test_cancel_in_dialog_prevents_action(qtbot, setup):
    win, services, fake, out = setup
    fake.chat_script = [tool_call_chunks("create_text_file", {"filename": "x.txt", "content": "x"}),
                        text_chunks("Understood, not created.")]
    start(qtbot, win, "create x.txt")
    qtbot.waitUntil(lambda: bool(win._active_dialogs), timeout=5000)
    dialog = next(iter(win._active_dialogs))
    with qtbot.waitSignal(win.controller.generation_finished, timeout=10000):
        qtbot.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
    assert not (out / "x.txt").exists()
    state, text = step_texts(win)[0]
    assert state == "failed" and "You declined, so nothing was done." in text


def test_stop_while_waiting_for_approval(qtbot, setup):
    win, services, fake, out = setup
    fake.chat_script = [tool_call_chunks("create_text_file", {"filename": "y.txt", "content": "y"}),
                        text_chunks("should not be requested")]
    start(qtbot, win, "create y.txt")
    qtbot.waitUntil(lambda: bool(win._active_dialogs), timeout=5000)
    with qtbot.waitSignal(win.controller.generation_finished, timeout=10000) as blocker:
        qtbot.mouseClick(win.chat_view.stop_button, Qt.MouseButton.LeftButton)
    assert blocker.args[1].status == "cancelled"
    qtbot.waitUntil(lambda: not win._active_dialogs, timeout=2000)
    assert not (out / "y.txt").exists()
    assert services.audit.recent(1)[0].decision == "cancelled"
    assert len([r for r in fake.requests if r["path"] == "/api/chat"]) == 1


def test_gui_responsive_while_tool_runs(qtbot, setup):
    win, services, fake, out = setup
    fake.chat_script = [tool_call_chunks("slow_lookup", {}), text_chunks("The answer is 42.")]
    timer, ticks = ticker()
    start(qtbot, win, "look it up")
    qtbot.waitUntil(lambda: any(state == "running" for state, _ in step_texts(win)), timeout=5000)
    assert "Running: Looking something up slowly" in win.chat_view.status_label.text()
    with qtbot.waitSignal(win.controller.generation_finished, timeout=10000):
        pass
    timer.stop()
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) > 30 and max(gaps) < 0.5
    assert step_texts(win)[0] == ("ok", "✓  Looking something up slowly — Found the answer")


def test_tool_activity_view(qtbot, setup):
    win, services, fake, out = setup
    fake.chat_script = [tool_call_chunks("read_text_file", {"path": "a.txt"}), text_chunks("That tool is disabled.")]
    start(qtbot, win, "read a.txt")
    qtbot.waitSignal(win.controller.generation_finished, timeout=10000).wait()
    win.show_page("activity")
    view = win.activity_view
    assert view.table.rowCount() == 1
    assert [view.table.item(0, c).text() for c in (1, 2, 3)] == ["read_text_file", "Denied by permissions", "Not run"]
    view.failures_only.setChecked(True)
    assert view.table.rowCount() == 1
    services.registry.invoke("get_datetime", {})
    view.refresh()
    assert view.table.rowCount() == 1  # the successful call is filtered out
    view.failures_only.setChecked(False)
    assert view.table.rowCount() == 2 and view.table.item(0, 3).text() == "Succeeded"


def test_saved_tool_steps_survive_reload(qtbot, setup):
    win, services, fake, out = setup
    fake.chat_script = [tool_call_chunks("get_datetime", {}), text_chunks("It is today.")]
    start(qtbot, win, "date?")
    qtbot.waitSignal(win.controller.generation_finished, timeout=10000).wait()
    conv = win.chat_view.conversation_id
    win.new_conversation()
    win.chat_view.show_conversation(conv)
    qtbot.waitUntil(lambda: len(message_widgets(win)) == 2, timeout=5000)
    steps = [label.text() for label in message_widgets(win)[-1]._steps.values()]
    assert steps and steps[0].startswith("✓  Checking the current date and time")
