"""In-process imitation of an OpenAI-compatible chat API (/v1/models, /v1/chat/completions with SSE)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

API_KEY = "sk-test-0123456789abcdefghij"


def text_events(text: str, size: int = 5, tokens: int = 12) -> list[dict[str, Any]]:
    events = [{"choices": [{"index": 0, "delta": {"content": text[i:i + size]}}]} for i in range(0, len(text), size)]
    events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    events.append({"choices": [], "usage": {"prompt_tokens": 30, "completion_tokens": tokens}})
    return events


def tool_events(name: str, arguments: dict, call_id: str = "call_abc123") -> list[dict[str, Any]]:
    raw = json.dumps(arguments)
    half = len(raw) // 2
    return [
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": call_id, "type": "function",
                                                            "function": {"name": name, "arguments": ""}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": raw[:half]}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": raw[half:]}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]


class FakeOpenAI:
    def __init__(self, models=("kimi-k3", "kimi-k2")):
        self.models = list(models)
        self.script: list[Any] = []
        self.requests: list[dict[str, Any]] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorised(self) -> bool:
                if self.headers.get("Authorization") != f"Bearer {API_KEY}":
                    self._json(401, {"error": {"message": "Invalid Authentication", "type": "invalid_authentication_error"}})
                    return False
                return True

            def do_GET(self):  # noqa: N802
                fake.requests.append({"path": self.path, "auth": self.headers.get("Authorization")})
                if not self._authorised():
                    return
                if self.path == "/v1/models":
                    self._json(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in fake.models]})
                else:
                    self._json(404, {"error": {"message": "not found"}})

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.requests.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
                if not self._authorised():
                    return
                if body.get("model") not in fake.models:
                    self._json(404, {"error": {"message": f"model {body.get('model')} not found"}})
                    return
                step = fake.script.pop(0) if fake.script else text_events("Hello from Kimi.")
                if isinstance(step, tuple) and step[0] == "error":
                    self._json(step[1], {"error": {"message": step[2]}})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event in step:
                    line = event if isinstance(event, str) else "data: " + json.dumps(event)
                    self.wfile.write((line + "\n\n").encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")

        return Handler
