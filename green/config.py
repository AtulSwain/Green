"""Validated application configuration.

Configuration is stored as JSON in the user's config directory, separate from the
SQLite database that holds conversations, memories and reminders. Every value is
validated with Pydantic before use. Credentials are never stored here.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

log = logging.getLogger(__name__)

DEFAULT_MODEL = "qwen3:8b"
FALLBACK_MODEL = "qwen3:4b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\-]{0,199}$")
_APP_NAME_RE = re.compile(r"^[\w .\-]{1,64}$")
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9\-]{0,61}[a-z0-9])?)*$")


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.strip("[]").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class _Section(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class OllamaSettings(_Section):
    base_url: str = DEFAULT_OLLAMA_URL
    model: str = DEFAULT_MODEL
    request_timeout_seconds: float = Field(180.0, ge=5, le=3600)
    connect_timeout_seconds: float = Field(5.0, ge=1, le=60)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    top_p: float = Field(0.9, gt=0.0, le=1.0)
    num_ctx: int = Field(8192, ge=512, le=262144)
    num_predict: int = Field(-1, ge=-1, le=32768, description="-1 lets the model decide")
    think: bool = Field(False, description="Enable the model's reasoning mode (slower)")
    keep_alive: str = Field("30m", pattern=r"^-?\d+[smh]?$")

    @field_validator("base_url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("Ollama URL must start with http:// or https://")
        if not parsed.hostname:
            raise ValueError("Ollama URL must include a host name")
        if parsed.username or parsed.password:
            raise ValueError("Do not put credentials in the Ollama URL")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Ollama URL must not contain a path, query or fragment")
        try:
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Ollama URL has an invalid port") from exc
        return value

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        value = value.strip()
        if not _MODEL_NAME_RE.match(value):
            raise ValueError("Model name contains invalid characters")
        return value

    @property
    def is_local(self) -> bool:
        return is_loopback_host(urlparse(self.base_url).hostname)


class UISettings(_Section):
    theme: Literal["dark", "light"] = "dark"
    font_point_size: int = Field(10, ge=8, le=20)
    send_on_enter: bool = True
    close_to_tray: bool = False  # closing the window keeps GREEN running in the system tray


class VoiceSettings(_Section):
    enabled: bool = False
    stt_model: str = Field("base.en", min_length=1, max_length=500)
    stt_device: Literal["cpu", "cuda", "auto"] = "cpu"
    stt_compute_type: Literal["int8", "int8_float16", "float16", "float32", "default"] = "int8"
    stt_language: str = Field("en", max_length=8)
    allow_model_download: bool = False
    piper_model_path: str = ""
    speak_responses: bool = False  # read every reply aloud (not only replies to voice messages)
    muted: bool = False
    auto_submit_transcript: bool = False
    max_recording_seconds: int = Field(30, ge=1, le=300)
    # Speech output: "system" uses the voices built into Windows (no download); "piper" uses a Piper voice file.
    tts_engine: Literal["system", "piper"] = "system"
    system_voice: str = Field("", max_length=200)
    speech_rate: float = Field(0.0, ge=-1.0, le=1.0)
    # Voice conversation: a spoken message is sent automatically and its reply is read aloud.
    conversation_mode: bool = True
    auto_stop_on_silence: bool = True
    silence_seconds: float = Field(1.5, ge=0.5, le=5.0)


class ToolSettings(_Section):
    # Deny by default: only side-effect-free status tools start enabled.
    enabled_tools: dict[str, bool] = Field(
        default_factory=lambda: {
            # Read-only and side-effect free:
            "get_datetime": True, "get_app_info": True, "get_system_info": True, "calculate": True,
            # Only write to GREEN's own database, and always ask first:
            "create_reminder": True, "save_memory": True,
        }
    )
    approved_directories: list[str] = Field(default_factory=list)
    output_directory: str = ""
    approved_applications: dict[str, str] = Field(default_factory=dict)
    allowed_websites: list[str] = Field(default_factory=list)
    # When true, open_website may open sites that are not on the allow-list after the user confirms.
    confirm_unlisted_websites: bool = True
    max_read_bytes: int = Field(200_000, ge=1_000, le=5_000_000)
    max_results: int = Field(200, ge=1, le=2_000)

    @field_validator("approved_directories")
    @classmethod
    def _validate_dirs(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            raw = raw.strip()
            if not raw:
                continue
            if not Path(raw).expanduser().is_absolute():
                raise ValueError(f"Approved directory must be an absolute path: {raw}")
            if raw not in cleaned:
                cleaned.append(raw)
        return cleaned

    @field_validator("output_directory")
    @classmethod
    def _validate_output(cls, value: str) -> str:
        value = value.strip()
        if value and not Path(value).expanduser().is_absolute():
            raise ValueError("Output directory must be an absolute path")
        return value

    @field_validator("approved_applications")
    @classmethod
    def _validate_apps(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}
        for name, path in value.items():
            name = name.strip()
            path = path.strip()
            if not _APP_NAME_RE.match(name):
                raise ValueError(f"Invalid application name: {name!r}")
            if not Path(path).is_absolute():
                raise ValueError(f"Application path must be absolute: {path}")
            suffix = Path(path).suffix.lower()
            if sys.platform == "win32" and suffix != ".exe":
                # Scripts (.bat, .cmd, .ps1, .vbs) would allow arbitrary commands.
                raise ValueError(f"Only .exe programs can be approved: {path}")
            if suffix in {".bat", ".cmd", ".ps1", ".vbs", ".js", ".sh", ".py", ".lnk"}:
                raise ValueError(f"Scripts and shortcuts cannot be approved: {path}")
            cleaned[name] = path
        return cleaned

    @field_validator("allowed_websites")
    @classmethod
    def _validate_sites(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            host = normalize_site(raw)
            if host and host not in cleaned:
                cleaned.append(host)
        return cleaned


def normalize_site(raw: str) -> str:
    """Turn 'https://GitHub.com/foo' or 'github.com' into 'github.com'."""
    raw = raw.strip().lower()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").rstrip(".")
    if not _HOSTNAME_RE.match(host):
        raise ValueError(f"Invalid website host: {raw!r}")
    return host


class ContextSettings(_Section):
    history_messages: int = Field(20, ge=2, le=200)
    memory_items: int = Field(6, ge=0, le=50)
    max_tool_rounds: int = Field(4, ge=0, le=10)


class WorkspaceSettings(_Section):
    project_directory: str = ""

    @field_validator("project_directory")
    @classmethod
    def _validate_project(cls, value: str) -> str:
        value = value.strip()
        if value and not Path(value).expanduser().is_absolute():
            raise ValueError("Project directory must be an absolute path")
        return value


class AppConfig(_Section):
    config_version: int = 1
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    ui: UISettings = Field(default_factory=UISettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)
    tools: ToolSettings = Field(default_factory=ToolSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)


def format_validation_error(error: ValidationError) -> str:
    parts = []
    for item in error.errors():
        location = ".".join(str(p) for p in item["loc"])
        parts.append(f"{location}: {item['msg']}")
    return "; ".join(parts)


class ConfigStore:
    """Loads and atomically saves :class:`AppConfig`. Thread-safe snapshot access."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._config = AppConfig()
        self.warnings: list[str] = []

    def load(self) -> AppConfig:
        self.warnings = []
        if not self.path.exists():
            config = AppConfig()
        else:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                config = AppConfig.model_validate(data)
            except (OSError, json.JSONDecodeError, ValidationError) as exc:
                detail = format_validation_error(exc) if isinstance(exc, ValidationError) else str(exc)
                backup = self._preserve_invalid_file()
                message = f"Settings file was invalid and defaults are in use ({detail})."
                if backup:
                    message += f" The previous file was kept at {backup}."
                log.warning("Invalid configuration: %s", detail)
                self.warnings.append(message)
                config = AppConfig()
        with self._lock:
            self._config = config
        return config

    def _preserve_invalid_file(self) -> Path | None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"{self.path.name}.invalid-{stamp}")
        try:
            os.replace(self.path, target)
            return target
        except OSError:
            return None

    def get(self) -> AppConfig:
        with self._lock:
            return self._config.model_copy(deep=True)

    def save(self, config: AppConfig) -> None:
        config = AppConfig.model_validate(config.model_dump())  # re-validate
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(config.model_dump(mode="json"), indent=2)
        fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        with self._lock:
            self._config = config

    def update(self, mutate) -> AppConfig:
        """Apply ``mutate(config_copy)`` and save the validated result."""
        config = self.get()
        mutate(config)
        self.save(config)
        return self.get()
