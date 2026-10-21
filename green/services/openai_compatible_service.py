"""Client for online or local model services that implement the OpenAI-compatible chat API
(``/chat/completions`` with server-sent events). Used for providers such as Moonshot (Kimi),
OpenRouter, SiliconFlow, Groq, Together, or a local LM Studio server.

It exposes the same interface as :class:`OllamaService` so the chat engine does not care
which kind of model it talks to. Tool calls are normalised to Ollama's message format.
"""

from __future__ import annotations

import http.client
import json
import logging
import socket
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlparse

from ..config import OllamaSettings, ProviderSettings
from .ollama_service import (
    CancelToken,
    ChatChunk,
    GenerationCancelled,
    ModelInfo,
    OllamaError,
    OllamaResponseError,
    OllamaStatus,
    OllamaTimeoutError,
    OllamaUnavailableError,
    strip_thinking,
)

log = logging.getLogger(__name__)


class ProviderError(OllamaError):
    """Error from an online model service; the message is safe to show."""


def to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert GREEN's (Ollama-style) messages to the OpenAI format, pairing tool results with call ids."""
    out: list[dict[str, Any]] = []
    pending: list[str] = []
    for index, message in enumerate(messages):
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            calls, pending = [], []
            for position, call in enumerate(message["tool_calls"]):
                function = call.get("function") or {}
                call_id = call.get("id") or f"call_{index}_{position}"
                arguments = function.get("arguments", {})
                calls.append({
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": str(function.get("name", "")),
                        "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                    },
                })
                pending.append(call_id)
            out.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
        elif role == "tool":
            call_id = message.get("tool_call_id")
            if call_id in pending:
                pending.remove(call_id)
            elif pending:
                call_id = pending.pop(0)
            out.append({"role": "tool", "tool_call_id": call_id or "call_unknown", "content": message.get("content", "")})
        else:
            out.append({"role": role, "content": message.get("content", "")})
    return out


def _friendly_http_error(status: int, message: str, provider: ProviderSettings, model: str) -> ProviderError:
    name = provider.name
    if status == 401:
        return ProviderError(f"{name} rejected the API key. Check the key under Settings → Online models.")
    if status == 402:
        return ProviderError(f"{name} reports a payment or credit problem: {message[:200]}")
    if status == 403:
        return ProviderError(f"{name} refused the request (no access to this model?): {message[:200]}")
    if status == 404:
        return ProviderError(f"{name} does not know the model '{model}'. Check the model name: {message[:200]}")
    if status == 429:
        return ProviderError(f"{name} is rate-limiting requests or your quota is used up. Try again later.")
    if status >= 500:
        return ProviderError(f"{name} had a server error ({status}). Try again later.")
    return ProviderError(f"{name} returned an error ({status}): {message[:300]}")


class OpenAICompatibleService:
    def __init__(
        self,
        provider: ProviderSettings,
        api_key: str | None,
        settings_provider: Callable[[], OllamaSettings],
    ):
        self.provider = provider
        self._api_key = api_key
        self._settings_provider = settings_provider

    # -- HTTP -------------------------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "User-Agent": "GREEN-assistant"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _require_key(self) -> None:
        if not self._api_key and not self.provider.is_local:
            raise ProviderError(
                f"No API key is saved for {self.provider.name}. Add it under Settings → Online models."
            )

    def _open(self, method: str, path: str, body: dict | None, timeout: float, cancel: CancelToken | None = None):
        settings = self._settings_provider()
        parsed = urlparse(self.provider.base_url)
        base_path = parsed.path.rstrip("/")
        host = parsed.hostname or ""
        if parsed.scheme == "https":
            conn = http.client.HTTPSConnection(host, parsed.port or 443, timeout=settings.connect_timeout_seconds)
        else:
            conn = http.client.HTTPConnection(host, parsed.port or 80, timeout=settings.connect_timeout_seconds)
        try:
            conn.connect()
            conn.sock.settimeout(timeout)
            if cancel is not None:
                cancel._attach(conn.sock)
                if cancel.cancelled:
                    raise GenerationCancelled()
            conn.request(method, base_path + path, body=json.dumps(body).encode() if body is not None else None,
                         headers=self._headers())
            response = conn.getresponse()
        except GenerationCancelled:
            conn.close()
            raise
        except (TimeoutError, socket.timeout) as exc:
            conn.close()
            if cancel is not None and cancel.cancelled:
                raise GenerationCancelled() from exc
            raise OllamaTimeoutError(f"{self.provider.name} did not respond in time.") from exc
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            if cancel is not None and cancel.cancelled:
                raise GenerationCancelled() from exc
            raise OllamaUnavailableError(
                f"Cannot reach {self.provider.name} at {self.provider.host}. Check your internet connection."
            ) from exc
        if response.status >= 400:
            raw = response.read(65536).decode("utf-8", "replace")
            conn.close()
            try:
                data = json.loads(raw)
                error = data.get("error", data)
                message = error.get("message", raw) if isinstance(error, dict) else str(error)
            except (json.JSONDecodeError, AttributeError):
                message = raw
            raise _friendly_http_error(response.status, str(message), self.provider, (body or {}).get("model", ""))
        return conn, response

    # -- public interface (same as OllamaService) ------------------------------------------------
    def list_models(self) -> list[ModelInfo]:
        self._require_key()
        conn, response = self._open("GET", "/models", None, timeout=30)
        try:
            data = json.loads(response.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OllamaResponseError(f"{self.provider.name} returned an unreadable model list.") from exc
        finally:
            conn.close()
        items = data.get("data", []) if isinstance(data, dict) else []
        return [ModelInfo(name=str(item["id"])) for item in items if isinstance(item, dict) and item.get("id")]

    def model_capabilities(self, model: str | None = None) -> list[str] | None:
        return None  # OpenAI-compatible services do not report this; tools are tried and fall back if rejected

    def status(self) -> OllamaStatus:
        model = self.provider.model
        status = OllamaStatus(False, self.provider.base_url, model, self.provider.is_local)
        try:
            status.installed_models = self.list_models()
            status.reachable = True
        except OllamaError as exc:
            status.error = str(exc)
            return status
        listed = {m.name for m in status.installed_models}
        status.model_installed = bool(model) and (not listed or model in listed)
        if not model:
            status.error = f"Choose a model for {self.provider.name}."
        elif not status.model_installed:
            status.error = f"{self.provider.name} does not list the model '{model}'."
        return status

    def chat_stream(self, messages, cancel: CancelToken, tools=None) -> Iterator[ChatChunk]:
        self._require_key()
        settings = self._settings_provider()
        model = self.provider.model
        if not model:
            raise ProviderError(f"Choose a model for {self.provider.name} under Settings → Online models.")
        body: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(messages),
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": settings.temperature,
            "top_p": settings.top_p,
        }
        if settings.num_predict > 0:
            body["max_tokens"] = settings.num_predict
        if tools:
            body["tools"] = tools
        try:
            yield from self._chat(body, cancel, settings.request_timeout_seconds)
        except ProviderError as exc:
            if tools and ("tool" in str(exc).lower() or "function" in str(exc).lower()):
                raise ProviderError(f"{exc} (does not support tools)") from exc
            raise

    def _chat(self, body, cancel: CancelToken, timeout: float) -> Iterator[ChatChunk]:
        conn, response = self._open("POST", "/chat/completions", body, timeout, cancel)
        calls: dict[int, dict[str, Any]] = {}
        usage_tokens = 0
        first_token_at: float | None = None
        try:
            while True:
                if cancel.cancelled:
                    raise GenerationCancelled()
                try:
                    raw = response.readline()
                except (TimeoutError, socket.timeout) as exc:
                    if cancel.cancelled:
                        raise GenerationCancelled() from exc
                    raise OllamaTimeoutError(f"{self.provider.name} stopped responding (timed out).") from exc
                except (OSError, http.client.HTTPException) as exc:
                    if cancel.cancelled:
                        raise GenerationCancelled() from exc
                    raise OllamaUnavailableError(f"The connection to {self.provider.name} was lost.") from exc
                if not raw:
                    break
                line = raw.decode("utf-8", "replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    data = json.loads(payload)
                except json.JSONDecodeError as exc:
                    raise OllamaResponseError(f"{self.provider.name} sent malformed data.") from exc
                if isinstance(data, dict) and data.get("error"):
                    error = data["error"]
                    raise ProviderError(f"{self.provider.name}: {error.get('message', error) if isinstance(error, dict) else error}")
                usage = data.get("usage") if isinstance(data, dict) else None
                if isinstance(usage, dict):
                    usage_tokens = int(usage.get("completion_tokens") or 0)
                for choice in (data.get("choices") or []) if isinstance(data, dict) else []:
                    delta = choice.get("delta") or {}
                    content = delta.get("content") or ""
                    thinking = delta.get("reasoning_content") or delta.get("reasoning") or ""
                    for fragment in delta.get("tool_calls") or []:
                        slot = calls.setdefault(int(fragment.get("index", 0)), {"id": None, "name": "", "arguments": ""})
                        if fragment.get("id"):
                            slot["id"] = fragment["id"]
                        function = fragment.get("function") or {}
                        slot["name"] += function.get("name") or ""
                        slot["arguments"] += function.get("arguments") or ""
                    if content or thinking:
                        if first_token_at is None:
                            first_token_at = time.monotonic()
                        yield ChatChunk(content=content, thinking=str(thinking))
        finally:
            cancel._attach(None)
            conn.close()
        tool_calls = []
        for index in sorted(calls):
            slot = calls[index]
            try:
                arguments: Any = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                arguments = slot["arguments"]  # the registry reports invalid JSON back to the model
            call: dict[str, Any] = {"function": {"name": slot["name"], "arguments": arguments}}
            if slot["id"]:
                call["id"] = slot["id"]
            tool_calls.append(call)
        elapsed_ns = int((time.monotonic() - first_token_at) * 1e9) if first_token_at else 0
        yield ChatChunk(done=True, tool_calls=tool_calls, eval_count=usage_tokens,
                        eval_duration_ns=elapsed_ns if usage_tokens else 0)

    def complete(self, messages, cancel: CancelToken | None = None) -> str:
        cancel = cancel or CancelToken()
        return strip_thinking("".join(chunk.content for chunk in self.chat_stream(messages, cancel))).strip()
