from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("GREEN_TTS_ENGINE", "mock")  # Qt's built-in test speech engine; no audio device needed
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from green.app.bootstrap import build_services  # noqa: E402
from green.config import AppConfig, ConfigStore  # noqa: E402
from green.paths import AppPaths  # noqa: E402

from .fake_ollama import FakeOllama  # noqa: E402


@pytest.fixture
def app_paths(tmp_path: Path) -> AppPaths:
    paths = AppPaths(config_dir=tmp_path / "config", data_dir=tmp_path / "data")
    paths.ensure()
    return paths


@pytest.fixture
def fake_ollama():
    server = FakeOllama().start()
    yield server
    server.stop()


def _write_config(paths: AppPaths, mutate=None) -> None:
    config = AppConfig()
    if mutate:
        mutate(config)
    ConfigStore(paths.config_file).save(config)


@pytest.fixture
def services_factory(app_paths: AppPaths):
    created = []

    def make(ollama_url: str | None = None, mutate=None):
        def apply(config: AppConfig):
            if ollama_url:
                config.ollama.base_url = ollama_url
            config.ollama.connect_timeout_seconds = 2
            config.ollama.request_timeout_seconds = 10
            if mutate:
                mutate(config)

        _write_config(app_paths, apply)
        services = build_services(app_paths, configure_logs=False)
        created.append(services)
        return services

    yield make
    for services in created:
        services.shutdown()


@pytest.fixture
def services(services_factory, fake_ollama):
    return services_factory(fake_ollama.url)


@pytest.fixture
def closed_port_url() -> str:
    """URL of a local port with nothing listening."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"
