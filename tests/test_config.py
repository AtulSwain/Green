import json
import sys

import pytest
from pydantic import ValidationError

from green.config import AppConfig, ConfigStore, OllamaSettings, ToolSettings, normalize_site


def test_defaults_are_local_and_deny_by_default():
    config = AppConfig()
    assert config.ollama.base_url == "http://127.0.0.1:11434"
    assert config.ollama.model == "qwen3:8b"
    assert config.ollama.is_local
    enabled = {name for name, on in config.tools.enabled_tools.items() if on}
    assert enabled == {"get_datetime", "get_app_info", "get_system_info"}
    assert config.tools.approved_directories == []
    assert config.voice.enabled is False
    assert config.voice.allow_model_download is False


@pytest.mark.parametrize(
    "url",
    ["ftp://localhost:11434", "localhost:11434", "http://", "http://user:pw@localhost:11434",
     "http://localhost:11434/api", "http://localhost:99999"],
)
def test_invalid_ollama_urls_rejected(url):
    with pytest.raises(ValidationError):
        OllamaSettings(base_url=url)


def test_remote_url_is_flagged_not_local():
    assert not OllamaSettings(base_url="http://192.168.1.20:11434").is_local
    assert OllamaSettings(base_url="http://localhost:11434/").is_local
    assert OllamaSettings(base_url="http://[::1]:11434").is_local


@pytest.mark.parametrize("name", ["", "qwen3 8b", "qwen3;rm -rf", "../evil"])
def test_invalid_model_names_rejected(name):
    with pytest.raises(ValidationError):
        OllamaSettings(model=name)


def test_numeric_ranges_enforced():
    with pytest.raises(ValidationError):
        OllamaSettings(temperature=5)
    with pytest.raises(ValidationError):
        OllamaSettings(num_ctx=10)


def test_tool_settings_validation(tmp_path):
    with pytest.raises(ValidationError):
        ToolSettings(approved_directories=["relative/path"])
    with pytest.raises(ValidationError):
        ToolSettings(approved_applications={"evil": str(tmp_path / "run.bat")})
    with pytest.raises(ValidationError):
        ToolSettings(approved_applications={"bad name!": str(tmp_path / "app.exe")})
    ok = ToolSettings(allowed_websites=["https://Docs.Python.org/3/", "github.com", "github.com"])
    assert ok.allowed_websites == ["docs.python.org", "github.com"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only rule")
def test_only_exe_allowed_on_windows(tmp_path):
    with pytest.raises(ValidationError):
        ToolSettings(approved_applications={"notes": str(tmp_path / "notes.txt")})


def test_normalize_site_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_site("not a host!")


def test_store_roundtrip(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    store.load()
    store.update(lambda c: setattr(c.ollama, "model", "qwen3:4b"))
    reloaded = ConfigStore(tmp_path / "config.json")
    assert reloaded.load().ollama.model == "qwen3:4b"
    assert not reloaded.warnings


def test_invalid_file_falls_back_to_defaults_and_is_preserved(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ollama": {"temperature": 99}}))
    store = ConfigStore(path)
    config = store.load()
    assert config.ollama.temperature == AppConfig().ollama.temperature
    assert store.warnings and "invalid" in store.warnings[0].lower()
    preserved = list(tmp_path.glob("config.json.invalid-*"))
    assert len(preserved) == 1 and "99" in preserved[0].read_text()


def test_corrupt_json_does_not_crash(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    store = ConfigStore(path)
    assert store.load() == AppConfig()
    assert store.warnings


def test_save_revalidates(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    store.load()
    config = store.get()
    config.tools.approved_directories.append("relative")  # mutation bypasses assignment validation
    with pytest.raises(ValidationError):
        store.save(config)
    assert not (tmp_path / "config.json").exists()


def test_get_returns_copy(tmp_path):
    store = ConfigStore(tmp_path / "config.json")
    store.load()
    store.get().tools.enabled_tools["read_text_file"] = True
    assert "read_text_file" not in store.get().tools.enabled_tools
