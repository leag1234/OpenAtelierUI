import asyncio
import socketio
import logging
import os
import sys
import time
from redis import asyncio as aioredis
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from open_webui.models.users import Users, UserNameResponse
from open_webui.models.channels import Channels
from open_webui.models.chats import Chats
from open_webui.utils.redis import (
    get_sentinels_from_env,
    get_sentinel_url_from_env,
)

from open_webui.env import (
    ENABLE_WEBSOCKET_SUPPORT,
    WEBSOCKET_MANAGER,
    WEBSOCKET_REDIS_URL,
    WEBSOCKET_REDIS_LOCK_TIMEOUT,
    WEBSOCKET_SENTINEL_PORT,
    WEBSOCKET_SENTINEL_HOSTS,
)
from open_webui.utils.auth import get_current_user, get_verified_user
from open_webui.socket.utils import RedisDict, RedisLock

from open_webui.env import (
    GLOBAL_LOG_LEVEL,
    SRC_LOG_LEVELS,
)


logging.basicConfig(stream=sys.stdout, level=GLOBAL_LOG_LEVEL)
log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["SOCKET"])


if WEBSOCKET_MANAGER == "redis":
    if WEBSOCKET_SENTINEL_HOSTS:
        mgr = socketio.AsyncRedisManager(
            get_sentinel_url_from_env(
                WEBSOCKET_REDIS_URL, WEBSOCKET_SENTINEL_HOSTS, WEBSOCKET_SENTINEL_PORT
            )
        )
    else:
        mgr = socketio.AsyncRedisManager(WEBSOCKET_REDIS_URL)
    sio = socketio.AsyncServer(
        cors_allowed_origins=[],
        async_mode="asgi",
        transports=(["websocket"] if ENABLE_WEBSOCKET_SUPPORT else ["polling"]),
        allow_upgrades=ENABLE_WEBSOCKET_SUPPORT,
        always_connect=True,
        client_manager=mgr,
    )
else:
    sio = socketio.AsyncServer(
        cors_allowed_origins=[],
        async_mode="asgi",
        transports=(["websocket"] if ENABLE_WEBSOCKET_SUPPORT else ["polling"]),
        allow_upgrades=ENABLE_WEBSOCKET_SUPPORT,
        always_connect=True,
    )


# Timeout duration in seconds
TIMEOUT_DURATION = 3

# Dictionary to maintain the user pool

if WEBSOCKET_MANAGER == "redis":
    log.debug("Using Redis to manage websockets.")
    redis_sentinels = get_sentinels_from_env(
        WEBSOCKET_SENTINEL_HOSTS, WEBSOCKET_SENTINEL_PORT
    )
    SESSION_POOL = RedisDict(
        "open-webui:session_pool",
        redis_url=WEBSOCKET_REDIS_URL,
        redis_sentinels=redis_sentinels,
    )
    USER_POOL = RedisDict(
        "open-webui:user_pool",
        redis_url=WEBSOCKET_REDIS_URL,
        redis_sentinels=redis_sentinels,
    )
    USAGE_POOL = RedisDict(
        "open-webui:usage_pool",
        redis_url=WEBSOCKET_REDIS_URL,
        redis_sentinels=redis_sentinels,
    )

    clean_up_lock = RedisLock(
        redis_url=WEBSOCKET_REDIS_URL,
        lock_name="usage_cleanup_lock",
        timeout_secs=WEBSOCKET_REDIS_LOCK_TIMEOUT,
        redis_sentinels=redis_sentinels,
    )
    aquire_func = clean_up_lock.aquire_lock
    renew_func = clean_up_lock.renew_lock
    release_func = clean_up_lock.release_lock
else:
    SESSION_POOL = {}
    USER_POOL = {}
    USAGE_POOL = {}
    aquire_func = release_func = renew_func = lambda: True


async def periodic_usage_pool_cleanup():
    if not aquire_func():
        log.debug("Usage pool cleanup lock already exists. Not running it.")
        return
    log.debug("Running periodic_usage_pool_cleanup")
    try:
        while True:
            if not renew_func():
                log.error(f"Unable to renew cleanup lock. Exiting usage pool cleanup.")
                raise Exception("Unable to renew usage pool cleanup lock.")

            now = int(time.time())
            send_usage = False
            for model_id, connections in list(USAGE_POOL.items()):
                # Creating a list of sids to remove if they have timed out
                expired_sids = [
                    sid
                    for sid, details in connections.items()
                    if now - details["updated_at"] > TIMEOUT_DURATION
                ]

                for sid in expired_sids:
                    del connections[sid]

                if not connections:
                    log.debug(f"Cleaning up model {model_id} from usage pool")
                    del USAGE_POOL[model_id]
                else:
                    USAGE_POOL[model_id] = connections

                send_usage = True

            if send_usage:
                # Emit updated usage information after cleaning
                await _emit_verified("usage", {"models": get_models_in_use()})

            await asyncio.sleep(TIMEOUT_DURATION)
    finally:
        release_func()


app = socketio.ASGIApp(
    sio,
    socketio_path="/ws/socket.io",
)


def _socket_user(token):
    if not isinstance(token, str) or not token or token.startswith("sk-"):
        return None
    scope = {"type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/ws/socket.io", "raw_path": b"/ws/socket.io", "query_string": b"",
             "headers": [], "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 80), "state": {}}
    request = Request(scope)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    try:
        return get_verified_user(get_current_user(request, None, credentials))
    except (HTTPException, AttributeError, TypeError, ValueError):
        return None


def _socket_token(auth):
    token = auth.get("token") if isinstance(auth, dict) else None
    return token if isinstance(token, str) and token and not token.startswith("sk-") else None


def _session_identity(sid):
    session = SESSION_POOL.get(sid)
    if not isinstance(session, dict):
        return None
    user_id, role = session.get("id"), session.get("role")
    sessions = USER_POOL.get(user_id, []) if isinstance(user_id, str) and user_id else []
    if (not isinstance(user_id, str) or not user_id or not isinstance(role, str) or role not in {"user", "admin"}
            or not isinstance(sessions, (list, tuple, set)) or sid not in sessions):
        return None
    return session


async def _emit_verified(event, payload):
    for sid, session in list(SESSION_POOL.items()):
        user_id = session.get("id") if isinstance(session, dict) else None
        sessions = USER_POOL.get(user_id, []) if isinstance(user_id, str) and user_id else []
        if (isinstance(session, dict) and isinstance(session.get("role"), str) and session.get("role") in {"user", "admin"} and isinstance(sessions, (list, tuple, set))
                and sid in sessions):
            await sio.emit(event, payload, to=sid)


async def _emit_user_list():
    verified = []
    for sid, session in list(SESSION_POOL.items()):
        user_id = session.get("id") if isinstance(session, dict) else None
        sessions = USER_POOL.get(user_id, []) if isinstance(user_id, str) and user_id else []
        if (isinstance(session, dict) and isinstance(session.get("role"), str) and session.get("role") in {"user", "admin"} and isinstance(sessions, (list, tuple, set))
                and sid in sessions):
            verified.append((sid, user_id))
    user_ids = list(dict.fromkeys(user_id for _, user_id in verified))
    for sid, _ in verified:
        await sio.emit("user-list", {"user_ids": user_ids}, to=sid)


def _register_socket_user(sid, user):
    if (not isinstance(getattr(user, "id", None), str) or not user.id
            or not isinstance(getattr(user, "role", None), str) or user.role not in {"user", "admin"}):
        return False
    existing = SESSION_POOL.get(sid)
    if isinstance(existing, dict) and existing.get("id") != user.id:
        return False
    SESSION_POOL[sid] = user.model_dump()
    sessions = USER_POOL.get(user.id, [])
    sessions = list(sessions) if isinstance(sessions, (list, tuple, set)) else []
    if sid not in sessions:
        sessions.append(sid)
    USER_POOL[user.id] = sessions
    return True


async def disconnect_user_sockets(user_id):
    sessions = USER_POOL.get(user_id, []) if isinstance(user_id, str) and user_id else []
    if not isinstance(sessions, (list, tuple, set)):
        sessions = []
    owned = []
    for sid in list(sessions):
        session = SESSION_POOL.get(sid)
        if isinstance(session, dict) and session.get("id") == user_id:
            owned.append(sid)
    owned = list(dict.fromkeys(owned))
    for sid in owned:
        del SESSION_POOL[sid]
        for model_id, connections in list(USAGE_POOL.items()):
            if isinstance(connections, dict) and sid in connections:
                remaining = {key: value for key, value in connections.items() if key != sid}
                if remaining:
                    USAGE_POOL[model_id] = remaining
                else:
                    del USAGE_POOL[model_id]
        await sio.disconnect(sid, namespace="/")
    if user_id in USER_POOL:
        del USER_POOL[user_id]
    await _emit_user_list()


def get_models_in_use():
    # List models that are currently in use
    models_in_use = list(USAGE_POOL.keys())
    return models_in_use


@sio.on("usage")
async def usage(sid, data):
    if _session_identity(sid) is None or not isinstance(data, dict):
        return
    model_id = data.get("model")
    if not isinstance(model_id, str) or not model_id:
        return
    # Record the timestamp for the last update
    current_time = int(time.time())

    # Store the new usage data and task
    USAGE_POOL[model_id] = {
        **(USAGE_POOL[model_id] if model_id in USAGE_POOL else {}),
        sid: {"updated_at": current_time},
    }

    # Broadcast the usage data to all clients
    await _emit_verified("usage", {"models": get_models_in_use()})


@sio.event
async def connect(sid, environ, auth):
    user = _socket_user(_socket_token(auth))
    if user and _register_socket_user(sid, user):
        await _emit_user_list()
        await _emit_verified("usage", {"models": get_models_in_use()})


@sio.on("user-join")
async def user_join(sid, data):
    auth = data.get("auth") if isinstance(data, dict) else None
    user = _socket_user(_socket_token(auth))
    if user is None or not _register_socket_user(sid, user):
        return

    # Join all the channels
    channels = Channels.get_channels_by_user_id(user.id)
    log.debug(f"{channels=}")
    for channel in channels:
        await sio.enter_room(sid, f"channel:{channel.id}")

    # print(f"user {user.name}({user.id}) connected with session ID {sid}")

    await _emit_user_list()
    await _emit_verified("usage", {"models": get_models_in_use()})
    return {"id": user.id, "name": user.name}


@sio.on("join-channels")
async def join_channel(sid, data):
    auth = data.get("auth") if isinstance(data, dict) else None
    user = _socket_user(_socket_token(auth))
    if user is None or not _register_socket_user(sid, user):
        return

    # Join all the channels
    channels = Channels.get_channels_by_user_id(user.id)
    log.debug(f"{channels=}")
    for channel in channels:
        await sio.enter_room(sid, f"channel:{channel.id}")


@sio.on("channel-events")
async def channel_events(sid, data):
    if _session_identity(sid) is None or not isinstance(data, dict):
        return
    channel_id = data.get("channel_id")
    event_data = data.get("data")
    if not isinstance(channel_id, str) or not channel_id or not isinstance(event_data, dict):
        return
    room = f"channel:{channel_id}"
    participants = sio.manager.get_participants(
        namespace="/",
        room=room,
    )

    sids = [sid for sid, _ in participants]
    if sid not in sids:
        return

    event_type = event_data.get("type")

    if event_type == "typing":
        await sio.emit(
            "channel-events",
            {
                "channel_id": channel_id,
                "message_id": data.get("message_id", None),
                "data": event_data,
                "user": UserNameResponse(**SESSION_POOL[sid]).model_dump(),
            },
            room=room,
        )


@sio.on("user-list")
async def user_list(sid):
    if _session_identity(sid) is not None:
        await _emit_user_list()


@sio.event
async def disconnect(sid):
    if sid in SESSION_POOL:
        user = SESSION_POOL[sid]
        del SESSION_POOL[sid]

        user_id = user.get("id") if isinstance(user, dict) else None
        sessions = USER_POOL.get(user_id, []) if isinstance(user_id, str) else []
        remaining = [item for item in sessions if item != sid] if isinstance(sessions, (list, tuple, set)) else []
        if remaining:
            USER_POOL[user_id] = remaining
        elif isinstance(user_id, str) and user_id in USER_POOL:
            del USER_POOL[user_id]
        for model_id, connections in list(USAGE_POOL.items()):
            if isinstance(connections, dict) and sid in connections:
                keep = {key: value for key, value in connections.items() if key != sid}
                if keep: USAGE_POOL[model_id] = keep
                else: del USAGE_POOL[model_id]
        await _emit_user_list()
    else:
        pass
        # print(f"Unknown session ID {sid} disconnected")


def get_event_emitter(request_info, update_db=True):
    async def __event_emitter__(event_data):
        user_id = request_info["user_id"]
        session_ids = set()
        if isinstance(user_id, str) and user_id:
            pooled_sessions = USER_POOL.get(user_id, [])
            if isinstance(pooled_sessions, (list, tuple, set)):
                session_ids.update(
                    session_id for session_id in pooled_sessions
                    if isinstance(session_id, str) and session_id
                )
            requested_session = request_info.get("session_id")
            if isinstance(requested_session, str) and requested_session:
                session_ids.add(requested_session)
            owned_sessions = set()
            for session_id in session_ids:
                session = SESSION_POOL.get(session_id)
                if _session_identity(session_id) is not None and session.get("id") == user_id:
                    owned_sessions.add(session_id)
            session_ids = owned_sessions

        for session_id in session_ids:
            envelope = {
                "chat_id": request_info.get("chat_id", None),
                "message_id": request_info.get("message_id", None),
                "data": event_data,
            }
            await sio.emit("chat-events", envelope, to=session_id)
            if os.environ.get("OPENATELIER_EVENT_CHANNEL_COMPAT") == "1":
                await sio.emit("events", envelope, to=session_id)

        if update_db:
            if "type" in event_data and event_data["type"] == "status":
                Chats.add_message_status_to_chat_by_id_and_message_id(
                    request_info["chat_id"],
                    request_info["message_id"],
                    event_data.get("data", {}),
                )

            if "type" in event_data and event_data["type"] == "message":
                message = Chats.get_message_by_id_and_message_id(
                    request_info["chat_id"],
                    request_info["message_id"],
                )

                if message:
                    content = message.get("content", "")
                    content += event_data.get("data", {}).get("content", "")

                    Chats.upsert_message_to_chat_by_id_and_message_id(
                        request_info["chat_id"],
                        request_info["message_id"],
                        {
                            "content": content,
                        },
                    )

            if "type" in event_data and event_data["type"] == "replace":
                content = event_data.get("data", {}).get("content", "")

                Chats.upsert_message_to_chat_by_id_and_message_id(
                    request_info["chat_id"],
                    request_info["message_id"],
                    {
                        "content": content,
                    },
                )

    return __event_emitter__


def get_event_call(request_info):
    async def __event_caller__(event_data):
        user_id = request_info.get("user_id") if isinstance(request_info, dict) else None
        session_id = request_info.get("session_id") if isinstance(request_info, dict) else None
        if (
            not isinstance(user_id, str)
            or not user_id
            or not isinstance(session_id, str)
            or not session_id
        ):
            raise ValueError("Callback session unavailable")
        session = SESSION_POOL.get(session_id)
        if _session_identity(session_id) is None or session.get("id") != user_id:
            raise ValueError("Callback session unavailable")

        event_name = (
            "events"
            if os.environ.get("OPENATELIER_EVENT_CHANNEL_COMPAT") == "1"
            else "chat-events"
        )
        response = await sio.call(
            event_name,
            {
                "chat_id": request_info.get("chat_id", None),
                "message_id": request_info.get("message_id", None),
                "data": event_data,
            },
            to=session_id,
        )
        return response

    return __event_caller__


get_event_caller = get_event_call


def get_user_id_from_session_pool(sid):
    user = SESSION_POOL.get(sid)
    if user:
        return user["id"]
    return None


def get_user_ids_from_room(room):
    active_session_ids = sio.manager.get_participants(
        namespace="/",
        room=room,
    )

    active_user_ids = list(dict.fromkeys(
        SESSION_POOL[session_id[0]]["id"]
        for session_id in active_session_ids
        if _session_identity(session_id[0]) is not None
    ))
    return active_user_ids


def get_active_status_by_user_id(user_id):
    if user_id in USER_POOL:
        return True
    return False
