"""Additional model services (OpenAI-compatible), API key storage and online privacy rules."""

import json
import logging

import pytest
from pydantic import ValidationError

from green.config import AppConfig, ConfigStore, ProviderSettings
from green.services.model_router import is_cloud_model_name
from green.services.ollama_service import CancelToken, OllamaError
from green.services.openai_compatible_service import to_openai_messages
from green.services.secret_store import SecretStore, SecretStoreError

from .fake_openai import API_KEY, FakeOpenAI, text_events, tool_events


class FakeKeyring:
    def __init__(self):
        self.data = {}

    def get_password(self, service, account):
        return self.data.get((service, account))

    def set_password(self, service, account, value):
        self.data[(service, account)] = value

    def delete_password(self, service, account):
        self.data.pop((service, account), None)


@pytest.fixture
def fake_api():
    server = FakeOpenAI()
    yield server
    server.stop()


@pytest.fixture
def online(services, fake_api, monkeypatch):
    """Services with an 'online' provider (the fake server, flagged online via a patched is_local)."""
    monkeypatch.setattr(ProviderSettings, "is_local", property(lambda self: False))
    services.secrets._backend = FakeKeyring()
    services.secrets.set("moonshot", API_KEY)
    provider = {"id": "moonshot", "name": "Moonshot", "base_url": fake_api.url, "models": ["kimi-k3"],
                "model": "kimi-k3", "consent_given": True}

    def mutate(c):
        c.providers = [ProviderSettings(**provider)]
        c.active_provider = "moonshot"

    services.config_store.update(mutate)
    return services


def ask(services, text, **kwargs):
    conv = services.conversations.create()
    services.conversations.add_message(conv.id, "user", text)
    return conv.id, services.engine.generate(conv.id, CancelToken(), **kwargs)


# -- configuration ---------------------------------------------------------------------------------

def test_provider_validation():
    ok = ProviderSettings(id="moonshot", name="Moonshot", base_url="https://api.moonshot.ai/v1/")
    assert ok.base_url == "https://api.moonshot.ai/v1" and not ok.is_local
    assert ProviderSettings(id="lm", name="LM Studio", base_url="http://127.0.0.1:1234/v1").is_local
    for bad in ("http://api.example.com/v1", "ftp://x", "https://user:pw@x.com/v1", "https://x.com/v1?key=1"):
        with pytest.raises(ValidationError):
            ProviderSettings(id="x", name="x", base_url=bad)
    for bad_id in ("Moon Shot", "ollama", "-x", ""):
        with pytest.raises(ValidationError):
            ProviderSettings(id=bad_id, name="x", base_url="https://x.com")
    with pytest.raises(ValidationError):
        ProviderSettings(id="x", name="x", base_url="https://x.com", models=["bad model"])


def test_active_provider_falls_back_when_removed():
    config = AppConfig.model_validate({"active_provider": "gone"})
    assert config.active_provider == ""
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"providers": [{"id": "a", "name": "A", "base_url": "https://a.com"},
                                                {"id": "a", "name": "B", "base_url": "https://b.com"}]})


def test_api_key_never_written_to_settings_file(online, app_paths):
    text = app_paths.config_file.read_text()
    assert API_KEY not in text and "moonshot" in text
    reloaded = ConfigStore(app_paths.config_file)
    assert reloaded.load().active_provider == "moonshot"


def test_secret_store():
    store = SecretStore(FakeKeyring())
    assert store.available()[0] and not store.has("x")
    store.set("x", "  key-123  ")
    assert store.get("x") == "key-123"
    with pytest.raises(SecretStoreError):
        store.set("x", "   ")
    store.delete("x")
    assert store.get("x") is None

    class Broken:
        def get_password(self, *a):
            raise RuntimeError("no backend")

    ok, detail = SecretStore(Broken()).available()
    assert not ok and "No secure credential store" in detail


# -- client ----------------------------------------------------------------------------------------

def test_streaming_answer_and_stats(online, fake_api):
    fake_api.script = [text_events("Kimi here, happy to help!", tokens=12)]
    conv, result = ask(online, "hello")
    assert result.status == "complete" and result.text == "Kimi here, happy to help!"
    assert result.stats["online"] is True and result.stats["tokens"] == 12
    assert result.stats["model"] == "kimi-k3 via Moonshot"
    request = [r for r in fake_api.requests if r["path"] == "/v1/chat/completions"][-1]
    assert request["auth"] == f"Bearer {API_KEY}" and request["body"]["model"] == "kimi-k3"
    assert request["body"]["stream"] is True and request["body"]["messages"][0]["role"] == "system"
    stored = online.conversations.messages(conv)[-1]
    assert stored.metadata["online"] is True


def test_tool_call_round_trip_with_ids(online, fake_api):
    fake_api.script = [tool_events("calculate", {"expression": "6*7"}, call_id="call_xyz"), text_events("It is 42.")]
    _, result = ask(online, "6 times 7?")
    assert result.text == "It is 42." and result.tools_used[0]["ok"]
    second = [r for r in fake_api.requests if r["path"] == "/v1/chat/completions"][-1]["body"]["messages"]
    assistant, tool = second[-2], second[-1]
    assert assistant["tool_calls"][0]["id"] == "call_xyz"
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"expression": "6*7"}
    assert tool == {"role": "tool", "tool_call_id": "call_xyz", "content": tool["content"]}
    assert json.loads(tool["content"])["result"]["result"] == 42


def test_file_tools_withheld_from_online_models(online, fake_api, tmp_path):
    online.config_store.update(lambda c: (c.tools.enabled_tools.update({"read_text_file": True}),
                                          setattr(c.tools, "approved_directories", [str(tmp_path)])))
    ask(online, "read my files")
    tools = {t["function"]["name"] for t in fake_api.requests[-1]["body"]["tools"]}
    assert "read_text_file" not in tools and "calculate" in tools
    online.config_store.update(lambda c: setattr(c.tools, "allow_file_tools_online", True))
    ask(online, "read my files")
    assert "read_text_file" in {t["function"]["name"] for t in fake_api.requests[-1]["body"]["tools"]}


def test_local_provider_keeps_file_tools(services, fake_api, tmp_path):
    services.secrets._backend = FakeKeyring()
    services.secrets.set("lm", API_KEY)
    services.config_store.update(lambda c: (
        setattr(c, "providers", [ProviderSettings(id="lm", name="LM Studio", base_url=fake_api.url,
                                                  models=["kimi-k3"], model="kimi-k3")]),
        setattr(c, "active_provider", "lm"),
        c.tools.enabled_tools.update({"read_text_file": True}),
    ))
    assert not services.router.is_online()
    ask(services, "hi")
    assert "read_text_file" in {t["function"]["name"] for t in fake_api.requests[-1]["body"]["tools"]}


def test_wrong_key_is_explained(online, fake_api):
    online.secrets.set("moonshot", "sk-wrong-key-000000000000")
    _, result = ask(online, "hi")
    assert result.status == "error" and "rejected the API key" in result.error


def test_missing_key_is_explained(online, fake_api):
    online.secrets.delete("moonshot")
    _, result = ask(online, "hi")
    assert result.status == "error" and "No API key is saved for Moonshot" in result.error
    assert not [r for r in fake_api.requests if r["path"] == "/v1/chat/completions"]


@pytest.mark.parametrize("status, fragment", [(429, "rate-limiting"), (402, "payment"), (500, "server error")])
def test_provider_errors(online, fake_api, status, fragment):
    fake_api.script = [("error", status, "nope")]
    _, result = ask(online, "hi")
    assert result.status == "error" and fragment in result.error


def test_tools_rejected_falls_back(online, fake_api):
    fake_api.script = [("error", 400, "This model does not support function calling"), text_events("Plain")]
    _, result = ask(online, "hi")
    assert result.text == "Plain" and "rejected tool calling" in result.notices[0]
    assert "tools" not in fake_api.requests[-1]["body"]


def test_cancel_and_unreachable(online, fake_api, closed_port_url):
    cancel = CancelToken()
    cancel.cancel()
    conv = online.conversations.create()
    online.conversations.add_message(conv.id, "user", "x")
    assert online.engine.generate(conv.id, cancel).status == "cancelled"
    online.config_store.update(lambda c: setattr(c.providers[0], "base_url", closed_port_url + "/v1"))
    online.config_store.update(lambda c: setattr(c, "providers", list(c.providers)))
    _, result = ask(online, "hi")
    assert result.status == "error" and "Cannot reach Moonshot" in result.error


def test_status_and_model_list(online, fake_api):
    status = online.router.status()
    assert status.reachable and status.model_installed and [m.name for m in status.installed_models] == ["kimi-k3", "kimi-k2"]
    with pytest.raises(OllamaError):
        online.router.backend_for(online.config_store.get().providers[0], "bad-key").list_models()


def test_key_not_logged(online, fake_api, caplog):
    caplog.set_level(logging.DEBUG)
    fake_api.script = [("error", 500, "boom")]
    ask(online, "hi")
    assert API_KEY not in caplog.text


def test_message_conversion_pairs_ids():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "a", "arguments": {"x": 1}}},
            {"function": {"name": "b", "arguments": "{}"}, "id": "given"}]},
        {"role": "tool", "content": "ra", "tool_name": "a"},
        {"role": "tool", "content": "rb", "tool_name": "b", "tool_call_id": "given"},
    ]
    out = to_openai_messages(messages)
    ids = [c["id"] for c in out[1]["tool_calls"]]
    assert ids[1] == "given" and out[2]["tool_call_id"] == ids[0] and out[3]["tool_call_id"] == "given"
    assert out[1]["tool_calls"][0]["function"]["arguments"] == '{"x": 1}'


def test_ollama_cloud_models_count_as_online(services):
    assert is_cloud_model_name("kimi-k2.6:cloud") and is_cloud_model_name("gpt-oss:120b-cloud")
    assert not is_cloud_model_name("qwen3:8b")
    services.config_store.update(lambda c: setattr(c.ollama, "model", "kimi-k2.6:cloud"))
    assert services.router.is_online() and services.router.destination() == "Ollama's cloud service"
