"""GUI tests (pytest-qt, offscreen). The model is the in-process fake Ollama server."""

import time

import pytest
from PySide6.QtCore import Qt, QTimer

from green.ui.chat_view import MessageWidget
from green.ui.main_window import MainWindow

from .fake_ollama import text_chunks, tool_call_chunks


@pytest.fixture
def window(qtbot, services):
    win = MainWindow(services, start_timers=False)
    qtbot.addWidget(win)
    win.show()
    yield win
    win.shutdown()


def message_widgets(win):
    layout = win.chat_view.messages_layout
    return [layout.itemAt(i).widget() for i in range(layout.count()) if isinstance(layout.itemAt(i).widget(), MessageWidget)]


def send(qtbot, win, text):
    win.chat_view.input.setPlainText(text)
    with qtbot.waitSignal(win.controller.generation_finished, timeout=15000) as blocker:
        qtbot.mouseClick(win.chat_view.send_button, Qt.MouseButton.LeftButton)
    return blocker.args[1]


def test_launches_and_every_page_opens(qtbot, window):
    for key in window.pages:
        window.show_page(key)
        assert window.stack.currentWidget() is window.pages[key]
    assert window.windowTitle() == "GREEN 2.0.1"
    window.show_page("chat")
    assert window.chat_view.stack.currentWidget() is window.chat_view.empty_state
    assert not window.chat_view.send_button.isEnabled()  # nothing typed yet


def test_status_indicator_reflects_real_state(qtbot, window, fake_ollama):
    window.check_status()
    qtbot.waitUntil(lambda: "ready" in window.status_text.text(), timeout=5000)
    assert window.status_dot.property("state") == "ok"
    fake_ollama.models = []
    window.check_status()
    qtbot.waitUntil(lambda: "not installed" in window.status_text.text(), timeout=5000)
    assert window.status_dot.property("state") == "warning"


def test_send_message_and_receive_reply(qtbot, window, fake_ollama, services):
    fake_ollama.chat_script = [text_chunks("Hi! I am GREEN.")]
    result = send(qtbot, window, "Hello")
    assert result.status == "complete"
    widgets = message_widgets(window)
    assert [w.role for w in widgets] == ["user", "assistant"]
    assert widgets[1].raw == "Hi! I am GREEN."
    assert window.sidebar.list.count() == 1
    assert window.sidebar.list.item(0).text() == "Hello"
    assert window.chat_view.input.toPlainText() == ""


def test_stop_generation_and_ui_stays_responsive(qtbot, window, fake_ollama, services):
    fake_ollama.chat_script = [("slow", text_chunks("token " * 400, size=3), 0.01)]
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start(20)
    window.chat_view.input.setPlainText("Write a long essay")
    qtbot.mouseClick(window.chat_view.send_button, Qt.MouseButton.LeftButton)
    assert window.chat_view.stop_button.isVisible()
    qtbot.waitUntil(lambda: len(window.chat_view._stream_text) > 200, timeout=10000)
    # Duplicate submissions are ignored while busy.
    assert window.controller.send(window.chat_view.conversation_id, "again") is False
    with qtbot.waitSignal(window.controller.generation_finished, timeout=10000) as blocker:
        qtbot.mouseClick(window.chat_view.stop_button, Qt.MouseButton.LeftButton)
    timer.stop()
    assert blocker.args[1].status == "cancelled"
    assert window.chat_view.send_button.isVisible() and not window.chat_view.stop_button.isVisible()
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert len(ticks) > 10 and max(gaps) < 0.5, "event loop was blocked during generation"
    roles = [m.role for m in services.conversations.messages(window.chat_view.conversation_id)]
    assert roles == ["user", "assistant"]  # one user message despite the duplicate attempt


def test_connection_error_shows_retry(qtbot, services_factory, closed_port_url):
    services = services_factory(closed_port_url)
    win = MainWindow(services, start_timers=False)
    qtbot.addWidget(win)
    win.show()
    result = send(qtbot, win, "anyone there?")
    assert result.status == "error"
    assert win.chat_view.banner.isVisible()
    assert "Cannot reach Ollama" in win.chat_view.banner.label.text()
    assert win.chat_view.banner.action.text() == "Retry"
    win.shutdown()


def test_tool_confirmation_dialog_bridge(qtbot, window, fake_ollama, services, tmp_path):
    services.config_store.update(lambda c: (
        c.tools.enabled_tools.__setitem__("create_text_file", True),
        setattr(c.tools, "output_directory", str(tmp_path)),
    ))
    asked = []
    window.controller.confirm_handler = lambda request: asked.append(request) or True
    fake_ollama.chat_script = [tool_call_chunks("create_text_file", {"filename": "todo.txt", "content": "milk\n"}), text_chunks("Created.")]
    result = send(qtbot, window, "make a todo file")
    assert result.status == "complete" and asked
    assert asked[0].target.endswith("todo.txt")
    assert (tmp_path / "todo.txt").read_text() == "milk\n"
    assert "create_text_file" in message_widgets(window)[-1].meta.text()


def test_conversation_reload_and_delete(qtbot, window, fake_ollama, services):
    send(qtbot, window, "first chat")
    conv_id = window.chat_view.conversation_id
    window.new_conversation()
    assert window.chat_view.conversation_id is None
    window.sidebar.list.setCurrentRow(0)
    qtbot.waitUntil(lambda: len(message_widgets(window)) == 2, timeout=5000)
    assert window.chat_view.conversation_id == conv_id
    services.conversations.delete(conv_id)
    window.sidebar.reload()
    window._conversation_deleted(conv_id)
    assert window.chat_view.conversation_id is None and window.sidebar.list.count() == 0


def test_settings_validation_blocks_bad_values(qtbot, window, services):
    window.show_page("settings")
    view = window.settings_view
    view.base_url.setText("ftp://nope")
    assert view.save() is False
    assert "Not saved" in view.error_label.text()
    assert services.config_store.get().ollama.base_url.startswith("http://127.0.0.1")
    view.load()
    view.theme.setCurrentIndex(view.theme.findData("light"))
    assert view.save() is True
    assert services.config_store.get().ui.theme == "light"


def test_memory_view_lists_and_filters(qtbot, window, services):
    services.memory.add("Prefers concise answers", "preference")
    services.memory.add("Project Falcon uses Rust", "project_note")
    window.show_page("memory")
    view = window.memory_view
    assert view.table.rowCount() == 2
    view.search.setText("falcon")
    assert view.table.rowCount() == 1
    view.search.setText("zzz")
    assert view.table.rowCount() == 0 and view.empty.isVisible()


def test_permissions_toggle_changes_config(qtbot, window, services):
    window.show_page("permissions")
    table = window.permissions_view.tool_table
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == "read_text_file")
    table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    assert services.permissions.is_enabled("read_text_file")
    table.item(row, 0).setCheckState(Qt.CheckState.Unchecked)
    assert not services.permissions.is_enabled("read_text_file")


def test_due_reminder_is_shown_once(qtbot, window, services):
    from datetime import datetime, timedelta, timezone

    reminder = services.reminders.create("Stretch", datetime.now(timezone.utc) - timedelta(seconds=1))
    window.check_reminders()
    assert reminder.id in window._open_reminders
    box = window._open_reminders[reminder.id]
    box.close()
    window.check_reminders()
    assert reminder.id not in window._open_reminders


def test_shutdown_during_generation_is_prompt(qtbot, services, fake_ollama):
    fake_ollama.chat_script = [("hang", 20)]
    win = MainWindow(services, start_timers=False)
    qtbot.addWidget(win)
    win.chat_view.input.setPlainText("this will hang")
    qtbot.mouseClick(win.chat_view.send_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: win.controller.is_busy, timeout=2000)
    qtbot.wait(300)
    start = time.monotonic()
    win.shutdown()
    assert time.monotonic() - start < 5
