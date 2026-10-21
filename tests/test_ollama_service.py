import threading
import time

import pytest

from green.config import OllamaSettings
from green.services.ollama_service import (
    CancelToken,
    GenerationCancelled,
    ModelNotInstalledError,
    OllamaError,
    OllamaResponseError,
    OllamaService,
    OllamaTimeoutError,
    OllamaUnavailableError,
    model_names_match,
    strip_thinking,
)

from .fake_ollama import text_chunks


def make_service(url, **overrides):
    settings = OllamaSettings(base_url=url, connect_timeout_seconds=2, request_timeout_seconds=overrides.pop("timeout", 10), **overrides)
    return OllamaService(lambda: settings)


def test_status_ready(fake_ollama):
    status = make_service(fake_ollama.url).status()
    assert status.reachable and status.model_installed and status.ready
    assert status.version == "0.12.0"
    assert [m.name for m in status.installed_models] == ["qwen3:8b"]


def test_status_model_missing(fake_ollama):
    fake_ollama.models = ["llama3.2:3b"]
    status = make_service(fake_ollama.url).status()
    assert status.reachable and not status.model_installed
    assert "ollama pull qwen3:8b" in status.error


def test_connection_failure(closed_port_url):
    service = make_service(closed_port_url)
    status = service.status()
    assert not status.reachable
    assert "Cannot reach Ollama" in status.error
    with pytest.raises(OllamaUnavailableError):
        list(service.chat_stream([{"role": "user", "content": "hi"}], CancelToken()))


def test_streaming_chat(fake_ollama):
    fake_ollama.chat_script = [text_chunks("Hello there, friend.")]
    service = make_service(fake_ollama.url, temperature=0.2, num_predict=128)
    chunks = list(service.chat_stream([{"role": "user", "content": "hi"}], CancelToken()))
    assert "".join(c.content for c in chunks) == "Hello there, friend."
    assert chunks[-1].done
    body = fake_ollama.requests[-1]["body"]
    assert body["stream"] is True and body["model"] == "qwen3:8b"
    assert body["options"]["temperature"] == 0.2 and body["options"]["num_predict"] == 128
    assert body["think"] is False


def test_model_not_installed_on_chat(fake_ollama):
    fake_ollama.models = []
    with pytest.raises(ModelNotInstalledError) as info:
        list(make_service(fake_ollama.url).chat_stream([{"role": "user", "content": "x"}], CancelToken()))
    assert "ollama pull qwen3:8b" in str(info.value)


def test_out_of_memory_error_is_explained(fake_ollama):
    fake_ollama.chat_script = [("error", 500, "model requires more system memory (9.1 GiB) than is available (4.0 GiB)")]
    with pytest.raises(OllamaError, match="not enough memory"):
        list(make_service(fake_ollama.url).chat_stream([{"role": "user", "content": "x"}], CancelToken()))


def test_error_inside_stream(fake_ollama):
    fake_ollama.chat_script = [[{"message": {"content": "par"}, "done": False}, {"error": "runner crashed"}]]
    with pytest.raises(OllamaError, match="runner crashed"):
        list(make_service(fake_ollama.url).chat_stream([{"role": "user", "content": "x"}], CancelToken()))


def test_malformed_stream(fake_ollama):
    fake_ollama.chat_script = [["{not json"]]
    with pytest.raises(OllamaResponseError):
        list(make_service(fake_ollama.url).chat_stream([{"role": "user", "content": "x"}], CancelToken()))


def test_think_option_retried_for_models_without_reasoning(fake_ollama):
    fake_ollama.chat_script = [("error", 400, '"qwen3:8b" does not support thinking'), text_chunks("ok")]
    chunks = list(make_service(fake_ollama.url).chat_stream([{"role": "user", "content": "x"}], CancelToken()))
    assert "".join(c.content for c in chunks) == "ok"
    assert "think" not in fake_ollama.requests[-1]["body"]


def test_cancel_interrupts_blocked_read(fake_ollama):
    fake_ollama.chat_script = [("hang", 8)]
    service = make_service(fake_ollama.url, timeout=30)
    cancel = CancelToken()
    threading.Timer(0.3, cancel.cancel).start()
    start = time.monotonic()
    with pytest.raises(GenerationCancelled):
        list(service.chat_stream([{"role": "user", "content": "x"}], cancel))
    assert time.monotonic() - start < 3


def test_cancel_during_streaming(fake_ollama):
    fake_ollama.chat_script = [("slow", text_chunks("x" * 400, size=1), 0.01)]
    cancel = CancelToken()
    received = []
    with pytest.raises(GenerationCancelled):
        for chunk in make_service(fake_ollama.url).chat_stream([{"role": "user", "content": "x"}], cancel):
            received.append(chunk.content)
            if len(received) == 5:
                cancel.cancel()
    assert len(received) < 50



def test_read_timeout_short(fake_ollama, monkeypatch):
    # Use a sub-minimum timeout directly on the service to keep the test fast.
    settings = OllamaSettings(base_url=fake_ollama.url)
    object.__setattr__(settings, "request_timeout_seconds", 0.5)
    fake_ollama.chat_script = [("hang", 3)]
    with pytest.raises(OllamaTimeoutError):
        list(OllamaService(lambda: settings).chat_stream([{"role": "user", "content": "x"}], CancelToken()))


def test_pull_model_reports_progress(fake_ollama):
    seen = []
    make_service(fake_ollama.url).pull_model("qwen3:4b", CancelToken(), lambda s, d, t: seen.append((s, d, t)))
    assert ("downloading", 50, 100) in seen and seen[-1][0] == "success"


def test_helpers():
    assert strip_thinking("<think>plan</think>\nAnswer") == "Answer"
    assert strip_thinking("Answer <think>unfinished") == "Answer "
    assert model_names_match("llama3", "llama3:latest")
    assert not model_names_match("qwen3:8b", "qwen3:4b")


def test_model_capabilities(fake_ollama):
    service = make_service(fake_ollama.url)
    assert service.model_capabilities() == ["completion", "tools"]
    assert service.status().supports_tools is True
    fake_ollama.capabilities = ["completion"]
    assert service.status().supports_tools is False
    fake_ollama.capabilities = None
    assert service.model_capabilities() is None and service.status().supports_tools is None
    with pytest.raises(ModelNotInstalledError):
        service.model_capabilities("missing:1b")
