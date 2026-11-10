# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Bounded outbound protocol support for installed Apps."""

import asyncio
import hashlib
import ipaddress
import json
import os
import socket
import time
import uuid
from urllib.parse import urlparse

import aiohttp
import jwt
import socketio

from open_webui.models.apps import Apps, Manifest


MAX_MANIFEST_BYTES = 256 * 1024
MAX_CONNECTION_TOKEN = 8192


class AppProtocolError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _allowed_private_origins() -> set[str]:
    raw = os.environ.get("APP_ALLOWED_PRIVATE_ORIGINS", "")
    return {item.strip().rstrip("/") for item in raw.split(",") if item.strip()}


def _origin(parsed) -> str:
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme}://{parsed.hostname}{port}"


async def validate_app_url(value: str):
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        origin = _origin(parsed)
    except (ValueError, TypeError):
        raise AppProtocolError(
            502,
            "Invalid app URL. Use an HTTP(S) URL without credentials or a fragment.",
        )
    allowed = origin in _allowed_private_origins()
    if parsed.scheme != "https" and not allowed:
        raise AppProtocolError(
            502,
            "App endpoints require HTTPS. For local HTTP, configure APP_ALLOWED_PRIVATE_ORIGINS.",
        )
    if not allowed:
        try:
            loop = asyncio.get_running_loop()
            records = await loop.run_in_executor(
                None,
                lambda: socket.getaddrinfo(
                    parsed.hostname,
                    parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                ),
            )
            addresses = {record[4][0] for record in records}
        except OSError as error:
            raise AppProtocolError(502, "App hostname could not be resolved. Check DNS from the WebUI server.") from error
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise AppProtocolError(
                502,
                "App resolves to a private or reserved address. For local development, configure APP_ALLOWED_PRIVATE_ORIGINS.",
            )
    return parsed, origin


async def fetch_manifest(url: str, api_key: str) -> Manifest:
    await validate_app_url(url)
    timeout = aiohttp.ClientTimeout(total=10)
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, allow_redirects=False) as response:
                if response.status != 200:
                    raise AppProtocolError(
                        502,
                        f"App manifest endpoint returned HTTP {response.status}. Check the URL or enter details manually.",
                    )
                raw = await response.content.read(MAX_MANIFEST_BYTES + 1)
    except AppProtocolError:
        raise
    except asyncio.TimeoutError as error:
        raise AppProtocolError(502, "App request timed out. Check that the app server is running and reachable.") from error
    except aiohttp.ClientConnectorCertificateError as error:
        raise AppProtocolError(502, "App TLS verification failed. Check its certificate and hostname.") from error
    except aiohttp.ClientConnectorDNSError as error:
        raise AppProtocolError(502, "App hostname could not be resolved. Check DNS from the WebUI server.") from error
    except aiohttp.ClientError as error:
        raise AppProtocolError(502, "Could not reach the app server. Check its address, port and network access.") from error
    if len(raw) > MAX_MANIFEST_BYTES:
        raise AppProtocolError(502, "App response exceeds the allowed size.")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise AppProtocolError(502, "App returned an invalid response. Expected JSON.") from error
    try:
        return Manifest.model_validate(payload)
    except Exception as error:
        raise AppProtocolError(502, "App returned an invalid manifest. Check its app details or enter them manually.") from error


class AppConnectionManager:
    def __init__(self):
        self.clients: dict[str, socketio.AsyncClient] = {}
        self.states: dict[str, str] = {}

    def status(self, app_id: str) -> str:
        client = self.clients.get(app_id)
        if client is not None and client.connected:
            return "connected"
        return self.states.get(app_id, "disconnected")

    async def disconnect(self, app_id: str) -> None:
        client = self.clients.pop(app_id, None)
        self.states[app_id] = "disconnected"
        if client is not None and client.connected:
            await client.disconnect()

    async def connect(self, app_id: str, supplied_api_key: str | None = None):
        app = Apps.get(app_id)
        if app is None:
            raise AppProtocolError(404, "App not found")
        stored_api_key, stored_connection_token = Apps.credentials(app_id)
        api_key = supplied_api_key or stored_api_key
        if not api_key:
            raise AppProtocolError(400, "An app API key is required")
        url = app.data.get("url")
        manifest = Manifest.model_validate(app.data.get("manifest"))
        parsed, origin = await validate_app_url(url)
        socket_path = parsed.path.strip("/")
        socket_path = f"{socket_path}/socket.io" if socket_path else "socket.io"
        await self.disconnect(app_id)
        self.states[app_id] = "connecting"
        client = socketio.AsyncClient(reconnection=True)
        try:
            await client.connect(
                origin,
                headers={"Authorization": f"Bearer {api_key}"},
                transports=["websocket"],
                namespaces=["/apps"],
                socketio_path=socket_path,
                wait_timeout=20,
            )
            challenge = str(uuid.uuid4())
            reply = await client.call(
                "app:connect",
                {
                    "app_id": app_id,
                    "challenge": challenge,
                    "connection_token": stored_connection_token,
                },
                namespace="/apps",
                timeout=10,
            )
            if not isinstance(reply, dict) or reply.get("status") != 200 or not isinstance(reply.get("data"), dict):
                raise AppProtocolError(403, "App rejected its connection credential. Review credentials before reconnecting.")
            data = reply["data"]
            installation_id = str(uuid.UUID(str(data.get("installation_id"))))
            connection_token = data.get("connection_token")
            proof = data.get("proof")
            if not isinstance(connection_token, str) or not connection_token or len(connection_token) > MAX_CONNECTION_TOKEN:
                raise ValueError("invalid connection token")
            header = jwt.get_unverified_header(proof)
            if header.get("typ") != "oix-connect+jwt":
                raise ValueError("invalid proof type")
            claims = jwt.decode(
                proof,
                manifest.public_key,
                algorithms=["EdDSA"],
                audience="oix:connect",
                options={
                    "require": [
                        "aud", "sub", "app_id", "manifest_id", "challenge",
                        "token_hash", "iat", "exp",
                    ]
                },
            )
            if (
                claims["sub"] != installation_id
                or claims["app_id"] != app_id
                or claims["manifest_id"] != manifest.id
                or claims["challenge"] != challenge
                or claims["token_hash"] != hashlib.sha256(connection_token.encode()).hexdigest()
                or int(claims["exp"]) - int(claims["iat"]) > 60
                or int(claims["iat"]) > int(time.time()) + 5
                or (stored_connection_token is not None and app.data.get("installation_id") != installation_id)
            ):
                raise ValueError("proof claims mismatch")
            ready = await client.call("app:ready", {}, namespace="/apps", timeout=10)
            if ready != {"status": 200, "data": {"received": True}}:
                raise AppProtocolError(502, "App could not complete its bidirectional connection check")
            updated = Apps.set_connected(app_id, api_key, connection_token, installation_id)
            self.clients[app_id] = client
            self.states[app_id] = "connected"
            return updated
        except AppProtocolError:
            if client.connected:
                await client.disconnect()
            self.states[app_id] = "disconnected"
            raise
        except Exception as error:
            if client.connected:
                await client.disconnect()
            self.states[app_id] = "disconnected"
            raise AppProtocolError(
                502,
                "App connection failed. Check the API key, pinned public key and Socket.IO endpoint.",
            ) from error

    async def request(self, app_id: str, payload: dict):
        client = self.clients.get(app_id)
        if client is None or not client.connected:
            raise AppProtocolError(503, "App is disconnected; event was not sent")
        try:
            return await client.call("app:request", payload, namespace="/apps", timeout=3)
        except Exception as error:
            raise AppProtocolError(504, "App response timed out; execution outcome may be unknown") from error

    async def restore_active(self):
        for app in Apps.list():
            if not app.is_active:
                continue
            try:
                await self.connect(app.id)
            except AppProtocolError:
                self.states[app.id] = "disconnected"

    async def close(self):
        for app_id in list(self.clients):
            await self.disconnect(app_id)


AppConnections = AppConnectionManager()
