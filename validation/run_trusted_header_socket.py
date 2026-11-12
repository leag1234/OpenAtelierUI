#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Verify the trusted-header browser socket registration contract at runtime."""

from __future__ import annotations

import argparse
import json
from urllib.request import Request, urlopen

import socketio


def signin(base_url: str, email: str) -> tuple[str, str]:
    request = Request(
        base_url.rstrip("/") + "/api/v1/auths/signin",
        data=json.dumps(
            {"email": "ignored@example.invalid", "password": "synthetic-unused"}
        ).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Atlas-User": email,
            "X-Atlas-User-Role": "admin",
        },
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        value = json.load(response)
    token = value.get("token")
    user_id = value.get("id")
    if not isinstance(token, str) or not token:
        raise AssertionError("trusted-header signin did not return a token")
    if not isinstance(user_id, str) or not user_id:
        raise AssertionError("trusted-header signin did not return a user id")
    return token, user_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument(
        "--email", default="round91-runtime-admin@example.invalid"
    )
    args = parser.parse_args()

    token, expected_user_id = signin(args.base, args.email)
    client = socketio.Client(reconnection=False, logger=False, engineio_logger=False)
    try:
        # Deliberately omit transport authentication. Atlas obtains its session
        # through trusted headers and registers the browser after Socket.IO has
        # connected, using the existing user-join event.
        client.connect(
            args.base,
            socketio_path="ws/socket.io",
            # The packaged server defaults to a WebSocket-only transport.
            transports=["websocket"],
            wait_timeout=20,
        )
        acknowledgement = client.call(
            "user-join", {"auth": {"token": token}}, timeout=20
        )
        if not isinstance(acknowledgement, dict):
            raise AssertionError("user-join did not return an acknowledgement")
        if acknowledgement.get("id") != expected_user_id:
            raise AssertionError("user-join acknowledged a different identity")
        print(
            json.dumps(
                {
                    "status": "trusted_header_socket_registered",
                    "transport_auth_supplied": False,
                    "acknowledged_user": True,
                    "token_printed": False,
                },
                sort_keys=True,
            )
        )
    finally:
        if client.connected:
            client.disconnect()


if __name__ == "__main__":
    main()
