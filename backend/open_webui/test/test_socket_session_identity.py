# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Source-bound tests for authenticated realtime identity and revocation."""

from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _load(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            node.returns = None
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                arg.annotation = None
            selected.append(node)
    if {item.name for item in selected} != set(names):
        raise AssertionError(f"missing source functions: {set(names) - {item.name for item in selected}}")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)


class Socket:
    def __init__(self):
        self.emits, self.disconnected, self.rooms = [], [], []
        self.manager = types.SimpleNamespace(get_participants=lambda **_: [])

    async def emit(self, event, data, **kw):
        self.emits.append((event, data, kw.get("to")))

    async def disconnect(self, sid, **_):
        self.disconnected.append(sid)

    async def enter_room(self, sid, room):
        self.rooms.append((sid, room))

    async def call(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class RealtimeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.session_pool, self.user_pool, self.usage_pool = {}, {}, {}
        self.sio = Socket()
        self.sio.calls = []
        self.users = {
            "member-token": types.SimpleNamespace(id="member", name="Member", role="user", model_dump=lambda: {"id": "member", "name": "Member", "role": "user"}),
            "admin-token": types.SimpleNamespace(id="admin", name="Admin", role="admin", model_dump=lambda: {"id": "admin", "name": "Admin", "role": "admin"}),
            "pending-token": types.SimpleNamespace(id="pending", name="Pending", role="pending", model_dump=lambda: {"id": "pending", "name": "Pending", "role": "pending"}),
            "bad-user-record": types.SimpleNamespace(name="Broken"),
        }
        self.http_auth_calls = []
        def get_current_user(request, tasks, credentials):
            self.http_auth_calls.append((request.scope["path"], credentials.credentials))
            return self.users.get(credentials.credentials)
        def get_verified_user(user):
            return user if user is not None and user.role in {"user", "admin"} else None
        ns = {
            "SESSION_POOL": self.session_pool, "USER_POOL": self.user_pool,
            "USAGE_POOL": self.usage_pool, "sio": self.sio,
            "Request": lambda scope: types.SimpleNamespace(scope=scope),
            "HTTPAuthorizationCredentials": lambda **kw: types.SimpleNamespace(**kw),
            "HTTPException": type("HTTPException", (Exception,), {}),
            "get_current_user": get_current_user, "get_verified_user": get_verified_user,
            "Channels": types.SimpleNamespace(get_channels_by_user_id=lambda _id: [types.SimpleNamespace(id="private-channel")] if _id == "member" else []),
            "Chats": types.SimpleNamespace(), "UserNameResponse": lambda **kw: types.SimpleNamespace(model_dump=lambda: kw),
            "asyncio": asyncio, "time": types.SimpleNamespace(time=lambda: 1), "os": __import__("os"),
            "log": logging.getLogger("socket-test"), "TIMEOUT_DURATION": 60,
            "get_models_in_use": lambda: list(self.usage_pool),
        }
        socket_path = ROOT / "socket" / "main.py"
        _load(socket_path, {
            "_socket_user", "_socket_token", "_session_identity", "_emit_verified",
            "_emit_user_list", "_register_socket_user", "disconnect_user_sockets",
            "connect", "user_join", "join_channel", "usage", "user_list", "disconnect",
            "channel_events", "get_event_call",
        }, ns)
        self.ns = ns

    def test_quiet_transport_can_join_after_signin_and_stale_token_cannot_reenroll(self):
        ns = self.ns
        asyncio.run(ns["connect"]("sid-member", {}, None))
        self.assertEqual((self.session_pool, self.user_pool, self.sio.emits), ({}, {}, []))
        joined = asyncio.run(ns["user_join"]("sid-member", {"auth": {"token": "member-token"}}))
        self.assertEqual(joined, {"id": "member", "name": "Member"})
        self.assertEqual(self.session_pool["sid-member"]["id"], "member")
        self.assertEqual(self.user_pool, {"member": ["sid-member"]})
        self.assertTrue(self.http_auth_calls)
        before = list(self.sio.emits)
        self.users["stale-token"] = None  # Shared HTTP validator rejects revoked/invalid credentials.
        asyncio.run(ns["user_join"]("sid-stale", {"auth": {"token": "stale-token"}}))
        asyncio.run(ns["user_join"]("sid-bad-record", {"auth": {"token": "bad-user-record"}}))
        asyncio.run(ns["connect"]("sid-invalid", {}, {"token": "invalid"}))
        asyncio.run(ns["user_list"]("sid-anonymous"))
        asyncio.run(ns["usage"]("sid-anonymous", {"model": "private-model"}))
        self.assertNotIn("sid-stale", self.session_pool)
        self.assertNotIn("sid-bad-record", self.session_pool)
        self.assertNotIn("sid-invalid", self.session_pool)
        self.assertEqual(self.sio.emits, before)
        self.assertIsNone(ns["_socket_user"]("pending-token"))

    def test_revoke_disconnects_only_target_and_removes_callback_binding(self):
        ns = self.ns
        asyncio.run(ns["connect"]("sid-member", {}, {"token": "member-token"}))
        asyncio.run(ns["connect"]("sid-member-second", {}, {"token": "member-token"}))
        asyncio.run(ns["connect"]("sid-admin", {}, {"token": "admin-token"}))
        self.usage_pool["private-model"] = {
            "sid-member": {"updated_at": 1},
            "sid-member-second": {"updated_at": 1},
            "sid-admin": {"updated_at": 1},
        }
        asyncio.run(ns["disconnect_user_sockets"]("member"))
        self.assertEqual(self.sio.disconnected, ["sid-member", "sid-member-second"])
        self.assertNotIn("sid-member", self.session_pool)
        self.assertNotIn("sid-member-second", self.session_pool)
        self.assertEqual(self.session_pool["sid-admin"]["id"], "admin")
        self.assertNotIn("member", self.user_pool)
        self.assertEqual(self.user_pool, {"admin": ["sid-admin"]})
        self.assertEqual(self.usage_pool["private-model"], {"sid-admin": {"updated_at": 1}})
        self.assertEqual(self.sio.emits[-1], ("user-list", {"user_ids": ["admin"]}, "sid-admin"))
        caller = ns["get_event_call"]({"user_id": "member", "session_id": "sid-member"})
        with self.assertRaisesRegex(ValueError, "Callback session unavailable"):
            asyncio.run(caller({"type": "status"}))
        self.assertEqual(self.sio.calls, [])
        self.users["fresh-token"] = self.users["member-token"]
        joined = asyncio.run(ns["user_join"]("sid-fresh", {"auth": {"token": "fresh-token"}}))
        self.assertEqual(joined["id"], "member")
        self.assertEqual(self.session_pool["sid-fresh"]["id"], "member")

    def test_channel_room_and_broadcast_require_current_verified_session(self):
        ns = self.ns
        asyncio.run(ns["join_channel"]("sid-anon", {"auth": {"token": "invalid"}}))
        self.assertEqual(self.sio.rooms, [])
        asyncio.run(ns["channel_events"]("sid-anon", {"channel_id": "private", "data": {"type": "typing"}}))
        self.assertEqual(self.sio.emits, [])
        self.session_pool["sid-corrupt"] = {"id": "corrupt", "role": []}
        self.user_pool["corrupt"] = ["sid-corrupt"]
        asyncio.run(ns["user_list"]("sid-corrupt"))
        self.assertEqual(self.sio.emits, [])
        del self.session_pool["sid-corrupt"]
        del self.user_pool["corrupt"]
        asyncio.run(ns["user_join"]("sid-member", {"auth": {"token": "member-token"}}))
        self.assertEqual(self.sio.rooms, [("sid-member", "channel:private-channel")])
        self.sio.manager.get_participants = lambda **_: [("sid-member", "")]
        asyncio.run(ns["channel_events"]("sid-member", {"channel_id": "private-channel", "data": {"type": "typing"}}))
        self.assertEqual(self.sio.emits[-1][0:1], ("channel-events",))

    def test_admin_revoke_rotates_epoch_before_target_socket_disconnect(self):
        order = []
        async def disconnect_target(user_id):
            order.append(("disconnect", user_id))
        ns = {
            "revoke_user_session_epoch": lambda user_id: order.append(("epoch", user_id)),
            "disconnect_user_sockets": disconnect_target,
            "Depends": lambda value: value,
            "get_admin_user": object(),
        }
        route = ROOT / "routers" / "users.py"
        _load(route, {"revoke_user_sessions"}, ns)
        self.assertIsNone(asyncio.run(ns["revoke_user_sessions"]("member", user=object())))
        self.assertEqual(order, [("epoch", "member"), ("disconnect", "member")])


if __name__ == "__main__":
    unittest.main()
