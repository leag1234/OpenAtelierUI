#!/usr/bin/env python3
"""Small offline OpenAI/terminal upstream for container compatibility tests."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        return

    def _json(self, value, status=200):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            return self._json(
                {"object": "list", "data": [{"id": "compat-model", "object": "model"}]}
            )
        if self.path.startswith("/files/serve/home/user/requests/chatcmpl-"):
            if (
                self.headers.get("Authorization") != "Bearer synthetic-terminal-key"
                or not self.headers.get("X-User-Id")
            ):
                return self._json({"error": "unavailable"}, 403)
            body = b"synthetic terminal attachment\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._json({"error": "not found"}, 404)
        size = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(size) or b"{}")
        if request.get("stream"):
            chunks = [
                {"id": "chatcmpl-compat", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "synthetic "}, "finish_reason": None}]},
                {"id": "chatcmpl-compat", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "response"}, "finish_reason": "stop"}]},
            ]
            body = b"".join(
                b"data: " + json.dumps(item, separators=(",", ":")).encode() + b"\n\n"
                for item in chunks
            ) + b"data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(
            {
                "id": "chatcmpl-compat",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "synthetic response"},
                        "finish_reason": "stop",
                    }
                ],
            }
        )


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8081), Handler).serve_forever()
