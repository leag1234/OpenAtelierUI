#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Run a bounded local relay that supplies synthetic trusted headers."""

from __future__ import annotations

import argparse
import select
import socket
import socketserver


MAX_HEADER_BYTES = 128 * 1024


class Relay(socketserver.BaseRequestHandler):
    upstream: tuple[str, int]
    email: bytes
    role: bytes

    def handle(self) -> None:
        raw = b""
        while b"\r\n\r\n" not in raw:
            block = self.request.recv(65536)
            if not block:
                return
            raw += block
            if len(raw) > MAX_HEADER_BYTES:
                raise ValueError("request header exceeds the relay limit")
        header, pending = raw.split(b"\r\n\r\n", 1)
        lines = header.split(b"\r\n")
        kept = [
            line
            for line in lines[1:]
            if not line.lower().startswith(
                (b"x-atlas-user:", b"x-atlas-user-role:")
            )
        ]
        forwarded = b"\r\n".join(
            [
                lines[0],
                b"X-Atlas-User: " + self.email,
                b"X-Atlas-User-Role: " + self.role,
                *kept,
            ]
        ) + b"\r\n\r\n" + pending
        with socket.create_connection(self.upstream, timeout=10) as upstream:
            upstream.sendall(forwarded)
            while True:
                ready, _, _ = select.select([self.request, upstream], [], [], 120)
                if not ready:
                    return
                for source in ready:
                    block = source.recv(65536)
                    if not block:
                        return
                    target = upstream if source is self.request else self.request
                    target.sendall(block)


class ThreadingRelay(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = False
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--upstream-host", default="127.0.0.1")
    parser.add_argument("--upstream-port", type=int, required=True)
    parser.add_argument("--email", default="round91-browser@example.invalid")
    parser.add_argument("--role", choices=("admin", "user"), default="admin")
    args = parser.parse_args()
    Relay.upstream = (args.upstream_host, args.upstream_port)
    Relay.email = args.email.encode("ascii")
    Relay.role = args.role.encode("ascii")
    with ThreadingRelay((args.listen_host, args.listen_port), Relay) as server:
        print("trusted_header_proxy_ready", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
