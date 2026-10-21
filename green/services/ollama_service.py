"""Client for the local Ollama HTTP API.

All model communication goes through this module. It uses only the standard
library, streams NDJSON responses, and supports cancellation by shutting down
the socket from another thread so a blocked read returns immediately.

Ollama API reference: https://github.com/ollama/ollama/blob/main/docs/api.md
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import socket
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from ..config import OllamaSettings

log = logging.getLogger(__name__)

PULL_INSTRUCTIONS = "Install it from a terminal with:  ollama pull {model}"


class OllamaError(Exception):
    """Base error. ``str(error)`` is a concise message suitable for the UI."""


class OllamaUnavailableError(OllamaError):
    pass


class ModelNotInstalledError(OllamaError):
    def __init__(self, model: str):
        self.model = model
        super().__init__(f"The model '{model}' is not installed in Ollama. " + PULL_INSTRUCTIONS.format(model=model))


class OllamaTimeoutError(OllamaError):
    pass


class OllamaResponseError(OllamaError):
    pass


class GenerationCancelled(Exception):
    pass


class CancelToken:
    """Thread-safe cancellation flag that can also abort a blocking network read."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._socket: socket.socket | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()
        with self._lock:
            sock = self._socket
        if sock is not None:
            try:
                # Unblocks a recv() in progress on another thread.
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)

    def _attach(self, sock: socket.socket | None) -> None:
        with self._lock:
            self._socket = sock

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise GenerationCancelled()


@dataclass
class ChatChunk:
    content: str = ""
    thinking: str = ""
    tool_calls: list[Any] = field(default_factory=list)
    done: bool = False
    done_reason: str | None = None
    # Timing reported by Ollama in the final chunk (nanoseconds / token counts).
    eval_count: int = 0
    eval_duration_ns: int = 0
    prompt_eval_count: int = 0
    load_duration_ns: int = 0


@dataclass
class ModelInfo:
    name: str
    size_bytes: int = 0
    parameter_size: str = ""
    quantization: str = ""


@dataclass
class RunningModel:
    name: str
    size_bytes: int = 0
    size_vram_bytes: int = 0


@dataclass
class OllamaStatus:
    reachable: bool
    base_url: str
    model: str
    is_local: bool
    version: str = ""
    model_installed: bool = False
    installed_models: list[ModelInfo] = field(default_factory=list)
    error: str = ""
    supports_tools: bool | None = None  # None = Ollama did not report model capabilities

    @property
    def ready(self) -> bool:
        return self.reachable and self.model_installed


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def model_names_match(configured: str, installed: str) -> bool:
    if configured == installed:
        return True
    if ":" not in configured:
        return installed == f"{configured}:latest"
    return False


_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.S)


def strip_thinking(text: str) -> str:
    """Remove <think>…</think> reasoning blocks (including an unfinished trailing one)."""
    text = _THINK_BLOCK.sub("", text)
    start = text.find("<think>")
    if start != -1:
        text = text[:start]
    return text.lstrip() if text.startswith(("\n", " ")) else text


def _friendly_error(message: str, model: str) -> OllamaError:
    lowered = message.lower()
    if "not found" in lowered and ("model" in lowered or model.lower() in lowered):
        return ModelNotInstalledError(model)
    if "memory" in lowered:
        return OllamaError(
            "The model could not run because there is not enough memory. Close other programs or "
            "choose a smaller model (for example qwen3:4b) in Settings. Details: " + message[:300]
        )
    return OllamaError(f"Ollama reported an error: {message[:500]}")


class OllamaService:
    def __init__(self, settings_provider: Callable[[], OllamaSettings]):
        self._settings_provider = settings_provider

    @property
    def settings(self) -> OllamaSettings:
        return self._settings_provider()

    # -- low level -----------------------------------------------------------------
    def _connection(self, timeout: float) -> http.client.HTTPConnection:
        parsed = urlparse(self.settings.base_url)
        host = parsed.hostname or "127.0.0.1"
        if parsed.scheme == "https":
            return http.client.HTTPSConnection(host, parsed.port or 443, timeout=timeout)
        return http.client.HTTPConnection(host, parsed.port or 80, timeout=timeout)

    def _open(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        *,
        timeout: float,
        cancel: CancelToken | None = None,
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        settings = self.settings
        conn = self._connection(timeout)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        try:
            # Connect with the short connect timeout, then switch to the read timeout.
            conn.timeout = settings.connect_timeout_seconds
            conn.connect()
            conn.sock.settimeout(timeout)
            if cancel is not None:
                # Keep the raw socket: http.client may drop conn.sock once the response starts.
                cancel._attach(conn.sock)
            if cancel is not None and cancel.cancelled:
                raise GenerationCancelled()
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
        except GenerationCancelled:
            conn.close()
            raise
        except (TimeoutError, socket.timeout) as exc:
            conn.close()
            if cancel is not None and cancel.cancelled:
                raise GenerationCancelled() from exc
            raise OllamaTimeoutError(
                f"Ollama did not respond within the time limit at {settings.base_url}."
            ) from exc
        except (ConnectionError, OSError, http.client.HTTPException) as exc:
            conn.close()
            if cancel is not None and cancel.cancelled:
                raise GenerationCancelled() from exc
            raise OllamaUnavailableError(
                f"Cannot reach Ollama at {settings.base_url}. Make sure Ollama is installed and running "
                "(start the Ollama app or run 'ollama serve')."
            ) from exc
        if response.status >= 400:
            raw = response.read(65536).decode("utf-8", "replace")
            conn.close()
            try:
                message = json.loads(raw).get("error", raw)
            except (json.JSONDecodeError, AttributeError):
                message = raw
            if response.status == 404:
                model = (body or {}).get("model") or (body or {}).get("name") or settings.model
                raise ModelNotInstalledError(model) if "not found" in str(message).lower() else OllamaError(
                    f"Ollama endpoint not found ({path}). Your Ollama version may be too old."
                )
            raise _friendly_error(str(message) or f"HTTP {response.status}", settings.model)
        return conn, response

    def _get_json(self, path: str) -> dict[str, Any]:
        conn, response = self._open("GET", path, None, timeout=self.settings.connect_timeout_seconds + 5)
        try:
            data = json.loads(response.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OllamaResponseError("Ollama returned an unreadable response.") from exc
        finally:
            conn.close()
        if not isinstance(data, dict):
            raise OllamaResponseError("Ollama returned an unexpected response.")
        return data

    def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        conn, response = self._open("POST", path, body, timeout=self.settings.connect_timeout_seconds + 10)
        try:
            data = json.loads(response.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OllamaResponseError("Ollama returned an unreadable response.") from exc
        finally:
            conn.close()
        if not isinstance(data, dict):
            raise OllamaResponseError("Ollama returned an unexpected response.")
        return data

    def _stream(
        self, path: str, body: dict[str, Any], cancel: CancelToken, timeout: float
    ) -> Iterator[dict[str, Any]]:
        conn, response = self._open("POST", path, body, timeout=timeout, cancel=cancel)
        try:
            while True:
                if cancel.cancelled:
                    raise GenerationCancelled()
                try:
                    line = response.readline()
                except (TimeoutError, socket.timeout) as exc:
                    if cancel.cancelled:
                        raise GenerationCancelled() from exc
                    raise OllamaTimeoutError(
                        "The model stopped responding (timed out). Large models can take a while to "
                        "load; you can increase the timeout in Settings."
                    ) from exc
                except (OSError, http.client.HTTPException) as exc:
                    if cancel.cancelled:
                        raise GenerationCancelled() from exc
                    raise OllamaUnavailableError("The connection to Ollama was lost.") from exc
                if not line:
                    if cancel.cancelled:
                        raise GenerationCancelled()
                    return
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise OllamaResponseError("Ollama sent malformed data.") from exc
                if not isinstance(data, dict):
                    raise OllamaResponseError("Ollama sent malformed data.")
                if "error" in data:
                    raise _friendly_error(str(data["error"]), body.get("model", ""))
                yield data
                if data.get("done"):
                    return
        finally:
            cancel._attach(None)
            conn.close()

    # -- public API ---------------------------------------------------------------
    def version(self) -> str:
        return str(self._get_json("/api/version").get("version", ""))

    def list_models(self) -> list[ModelInfo]:
        data = self._get_json("/api/tags")
        models: list[ModelInfo] = []
        for item in data.get("models") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            details = item.get("details") or {}
            models.append(
                ModelInfo(
                    name=str(item["name"]),
                    size_bytes=int(item.get("size") or 0),
                    parameter_size=str(details.get("parameter_size") or ""),
                    quantization=str(details.get("quantization_level") or ""),
                )
            )
        return models

    def running_models(self) -> list[RunningModel]:
        data = self._get_json("/api/ps")
        result = []
        for item in data.get("models") or []:
            if isinstance(item, dict) and item.get("name"):
                result.append(
                    RunningModel(str(item["name"]), int(item.get("size") or 0), int(item.get("size_vram") or 0))
                )
        return result

    def model_capabilities(self, model: str | None = None) -> list[str] | None:
        """Capabilities Ollama reports for a model (e.g. ["completion", "tools"]).

        Returns None when this Ollama version does not report capabilities.
        Raises ModelNotInstalledError if the model is unknown to Ollama.
        """
        data = self._post_json("/api/show", {"model": model or self.settings.model})
        capabilities = data.get("capabilities")
        if not isinstance(capabilities, list):
            return None
        return [str(c) for c in capabilities]

    def status(self) -> OllamaStatus:
        settings = self.settings
        status = OllamaStatus(False, settings.base_url, settings.model, settings.is_local)
        try:
            status.version = self.version()
            status.installed_models = self.list_models()
            status.reachable = True
        except OllamaError as exc:
            status.error = str(exc)
            return status
        status.model_installed = any(model_names_match(settings.model, m.name) for m in status.installed_models)
        if not status.model_installed:
            status.error = str(ModelNotInstalledError(settings.model))
            return status
        try:
            capabilities = self.model_capabilities(settings.model)
            status.supports_tools = None if capabilities is None else "tools" in capabilities
        except OllamaError:
            status.supports_tools = None
        return status

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        cancel: CancelToken,
        tools: list[dict[str, Any]] | None = None,
    ) -> Iterator[ChatChunk]:
        settings = self.settings
        options: dict[str, Any] = {
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "num_ctx": settings.num_ctx,
        }
        if settings.num_predict != -1:
            options["num_predict"] = settings.num_predict
        body: dict[str, Any] = {
            "model": settings.model,
            "messages": messages,
            "stream": True,
            "options": options,
            "keep_alive": settings.keep_alive,
            "think": settings.think,
        }
        if tools:
            body["tools"] = tools
        try:
            yield from self._chat_chunks(body, cancel, settings.request_timeout_seconds)
        except OllamaError as exc:
            # Models without a reasoning mode reject the "think" option; retry without it.
            if "does not support thinking" in str(exc).lower() and "think" in body:
                del body["think"]
                yield from self._chat_chunks(body, cancel, settings.request_timeout_seconds)
            else:
                raise

    def _chat_chunks(self, body: dict[str, Any], cancel: CancelToken, timeout: float) -> Iterator[ChatChunk]:
        for data in self._stream("/api/chat", body, cancel, timeout):
            message = data.get("message") or {}
            if not isinstance(message, dict):
                raise OllamaResponseError("Ollama sent a malformed message.")
            calls = message.get("tool_calls") or []
            yield ChatChunk(
                content=str(message.get("content") or ""),
                thinking=str(message.get("thinking") or ""),
                tool_calls=calls if isinstance(calls, list) else [calls],
                done=bool(data.get("done")),
                done_reason=data.get("done_reason"),
                eval_count=_int(data.get("eval_count")),
                eval_duration_ns=_int(data.get("eval_duration")),
                prompt_eval_count=_int(data.get("prompt_eval_count")),
                load_duration_ns=_int(data.get("load_duration")),
            )

    def complete(self, messages: list[dict[str, Any]], cancel: CancelToken | None = None) -> str:
        """Collect a full non-tool response (used for summaries)."""
        cancel = cancel or CancelToken()
        parts = [chunk.content for chunk in self.chat_stream(messages, cancel)]
        return strip_thinking("".join(parts)).strip()

    def pull_model(
        self,
        name: str,
        cancel: CancelToken,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        """Download a model. Only call after the user explicitly approved the download."""
        body = {"model": name, "stream": True}
        for data in self._stream("/api/pull", body, cancel, timeout=600):
            if progress is not None:
                progress(str(data.get("status", "")), int(data.get("completed") or 0), int(data.get("total") or 0))
            if data.get("status") == "success":
                return
