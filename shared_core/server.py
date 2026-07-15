"""Loopback-only HTTP facade for the local Shared Core."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .core import ActionClass, SharedCore, TaskOwner


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], core: SharedCore):
        super().__init__(address, _Handler)
        self.core = core

    def start_in_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok", "service": "shared-core"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path == "/v1/sanitize":
            self._sanitize()
            return
        if self.path != "/v1/tasks":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
            task = self.server.core.create_task(
                owner=TaskOwner(payload["owner"]),
                session_id=str(payload["session_id"]),
                request=str(payload["request"]),
                action_class=ActionClass(payload["action_class"]),
            )
        except (KeyError, ValueError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_task"})
            return
        self._json(HTTPStatus.CREATED, {"id": task.id, "owner": task.owner.value, "state": task.state.value})

    def _sanitize(self) -> None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size))
            content = payload["content"]
            if not isinstance(content, str):
                raise ValueError("content must be a string")
        except (KeyError, ValueError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_content"})
            return
        sanitized = self.server.core.policy().sanitize(content)
        self._json(
            HTTPStatus.OK,
            {"content": sanitized.content, "finding_kinds": sorted(sanitized.finding_kinds)},
        )

    def _json(self, status: HTTPStatus, data: dict) -> None:
        body = json.dumps(data, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def create_server(core: SharedCore, *, host: str = "127.0.0.1", port: int = 8730) -> _Server:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Shared Core may bind only to loopback")
    return _Server((host, port), core)
