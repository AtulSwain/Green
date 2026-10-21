"""A small in-process HTTP server that imitates the parts of the Ollama API GREEN uses.

Tests use it instead of a real model so they exercise real HTTP, streaming and
socket-level cancellation without needing Ollama installed.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def text_chunks(text: str, size: int = 4) -> list[dict[str, Any]]:
    chunks = [{"message": {"role": "assistant", "content": text[i : i + size]}, "done": False} for i in range(0, len(text), size)]
    chunks.append({"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"})
    return chunks


def tool_call_chunks(name: str, arguments: Any) -> list[dict[str, Any]]:
    return [
        {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": arguments}}]}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"},
    ]


class FakeOllama:
    def __init__(self, models: list[str] | None = None):
        self.models = models if models is not None else ["qwen3:8b"]
        self.version = "0.12.0"
        # Each entry is consumed by one /api/chat call:
        #   list of chunk dicts | ("error", status, message) | ("hang", seconds) | ("slow", chunks, delay)
        self.chat_script: list[Any] = []
        self.requests: list[dict[str, Any]] = []
        self.pull_chunks: list[dict[str, Any]] = [
            {"status": "pulling manifest"},
            {"status": "downloading", "total": 100, "completed": 50},
            {"status": "success"},
        ]
        handler = self._handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    def start(self) -> FakeOllama:
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if self.path == "/api/version":
                    self._json(200, {"version": fake.version})
                elif self.path == "/api/tags":
                    self._json(200, {"models": [{"name": m, "size": 5_000_000_000, "details": {"parameter_size": "8B"}} for m in fake.models]})
                elif self.path == "/api/ps":
                    self._json(200, {"models": []})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                fake.requests.append({"path": self.path, "body": body})
                if self.path == "/api/pull":
                    self._stream(fake.pull_chunks)
                    return
                if self.path != "/api/chat":
                    self._json(404, {"error": "not found"})
                    return
                if body.get("model") not in fake.models:
                    self._json(404, {"error": f"model '{body.get('model')}' not found"})
                    return
                step = fake.chat_script.pop(0) if fake.chat_script else text_chunks("Hello from the fake model.")
                if isinstance(step, tuple) and step[0] == "error":
                    self._json(step[1], {"error": step[2]})
                    return
                if isinstance(step, tuple) and step[0] == "hang":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.end_headers()
                    self.wfile.flush()
                    deadline = time.time() + step[1]
                    while time.time() < deadline:
                        time.sleep(0.05)
                    return
                delay = 0.0
                if isinstance(step, tuple) and step[0] == "slow":
                    step, delay = step[1], step[2]
                self._stream(step, delay)

            def _stream(self, chunks, delay: float = 0.0) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                try:
                    for chunk in chunks:
                        line = chunk if isinstance(chunk, (bytes, str)) else json.dumps(chunk)
                        if isinstance(line, str):
                            line = line.encode()
                        self.wfile.write(line + b"\n")
                        self.wfile.flush()
                        if delay:
                            time.sleep(delay)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        return Handler
