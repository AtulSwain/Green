"""GUI for additional model services: settings dialog, model menu, consent, status and labels."""

import pytest
from PySide6.QtWidgets import QMessageBox

from green.config import ProviderSettings
from green.ui import chat_view as chat_view_module
from green.ui import settings_view as settings_module
from green.ui.main_window import MainWindow
from green.ui.provider_dialog import ProviderDialog

from .fake_openai import API_KEY, FakeOpenAI, text_events
from .test_gui import message_widgets, send
from .test_providers import FakeKeyring


@pytest.fixture
def fake_api():
    server = FakeOpenAI()
    yield server
    server.stop()


@pytest.fixture
def window(qtbot, services, monkeypatch):
    services.secrets._backend = FakeKeyring()
    monkeypatch.setattr(ProviderSettings, "is_local", property(lambda self: "localhost" in self.base_url))  # fake server counts as online
    win = MainWindow(services, start_timers=False)
    qtbot.addWidget(win)
    win.show()
    yield win
    win.shutdown()


def combo_texts(win):
    combo = win.chat_view.model_combo
    return [combo.itemText(i) for i in range(combo.count())]


def add_provider_via_settings(qtbot, win, fake_api, monkeypatch):
    class AutoDialog(ProviderDialog):
        def exec(self):
            self.name.setText("Moonshot")
            self.base_url.setText(fake_api.url)
            self.api_key.setText(API_KEY)
            self._fetch()
            qtbot.waitUntil(lambda: self.available.count() == 2, timeout=5000)
            self.available.setCurrentText("kimi-k3")
            self._add_selected()
            self._accept()
            return self.result()

    monkeypatch.setattr(settings_module, "ProviderDialog", AutoDialog)
    win.show_page("settings")
    win.settings_view._add_provider()


def test_add_service_in_settings(qtbot, window, services, fake_api, monkeypatch):
    add_provider_via_settings(qtbot, window, fake_api, monkeypatch)
    provider = services.config_store.get().provider("moonshot")
    assert provider.models == ["kimi-k3"] and provider.model == "kimi-k3" and not provider.consent_given
    assert services.secrets.get("moonshot") == API_KEY
    assert "key saved" in window.settings_view.provider_list.item(0).text()
    assert "🌐 Moonshot: kimi-k3" in combo_texts(window)  # model menu updated after saving


def test_online_model_needs_consent(qtbot, window, services, fake_api, monkeypatch):
    add_provider_via_settings(qtbot, window, fake_api, monkeypatch)
    combo = window.chat_view.model_combo
    index = combo.findText("🌐 Moonshot: kimi-k3")

    monkeypatch.setattr(chat_view_module.QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    combo.setCurrentIndex(index)
    combo.activated.emit(index)
    assert services.config_store.get().active_provider == ""  # declined: still local
    assert combo.currentText() == "qwen3:8b"

    monkeypatch.setattr(chat_view_module.QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Yes)
    combo.setCurrentIndex(index)
    combo.activated.emit(index)
    config = services.config_store.get()
    assert config.active_provider == "moonshot" and config.provider("moonshot").consent_given
    assert not window.chat_view.banner.isHidden() and "online" in window.chat_view.banner.label.text()

    window.check_status()
    qtbot.waitUntil(lambda: "kimi-k3 via Moonshot ready" in window.status_text.text(), timeout=5000)
    assert "messages leave this computer" in window.status_text.text()
    assert window.status_dot.property("state") == "warning"

    fake_api.script = [text_events("Hi from Kimi!")]
    send(qtbot, window, "hello")
    reply = message_widgets(window)[-1]
    assert reply.raw == "Hi from Kimi!" and "🌐 online" in reply.meta.text()

    local = combo.findText("qwen3:8b")
    combo.setCurrentIndex(local)
    combo.activated.emit(local)
    assert services.config_store.get().active_provider == "" and window.chat_view.banner.isHidden()


def test_remove_service(qtbot, window, services, fake_api, monkeypatch):
    add_provider_via_settings(qtbot, window, fake_api, monkeypatch)
    view = window.settings_view
    view.provider_list.setCurrentRow(0)
    monkeypatch.setattr(settings_module.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    view._remove_provider()
    assert services.config_store.get().providers == [] and services.secrets.get("moonshot") is None
    assert not any("Moonshot" in t for t in combo_texts(window))


def test_dialog_requires_key_and_models(qtbot, window, services):
    dialog = ProviderDialog(window, window.runner, services.router, services.secrets)
    qtbot.addWidget(dialog)
    dialog.base_url.setText("https://api.example.com/v1")
    dialog.models.setText("kimi-k3")
    dialog._accept()
    assert "API key is required" in dialog.error.text()
    dialog.api_key.setText("k")
    dialog.models.setText("")
    dialog._accept()
    assert "at least one model" in dialog.error.text()
    dialog.base_url.setText("http://api.example.com/v1")
    dialog.models.setText("kimi-k3")
    dialog._accept()
    assert "https" in dialog.error.text()
