# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
import asyncio

from open_webui.models.chats import Chats
from open_webui.socket.main import get_event_emitter
from open_webui.utils.middleware import process_chat_response


def is_supported_async_plugin_response(response):
    if not isinstance(response, dict):
        return False
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return False
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    return isinstance(content, str) and bool(content)


async def _emit_async_plugin_failure(metadata, message):
    failure = {"error": {"content": message}, "done": True}
    try:
        Chats.upsert_message_to_chat_by_id_and_message_id(
            metadata["chat_id"],
            metadata["message_id"],
            {"error": {"content": message}},
        )
    except Exception:
        pass
    await get_event_emitter(metadata)({"type": "chat:completion", "data": failure})


async def run_async_plugin_completion(
    request, completion_handler, form_data, user, metadata, model, events, tasks
):
    try:
        # Yield once so the request handler can return its accepted-task response.
        await asyncio.sleep(0)
        response = await completion_handler(request, form_data, user)
        if not is_supported_async_plugin_response(response):
            await _emit_async_plugin_failure(
                metadata,
                "The plugin returned a response that could not be processed.",
            )
            return
        await process_chat_response(
            request, response, form_data, user, metadata, model, events, tasks
        )
    except asyncio.CancelledError:
        try:
            await get_event_emitter(metadata)({"type": "task-cancelled"})
        except Exception:
            pass
        raise
    except Exception:
        try:
            await _emit_async_plugin_failure(
                metadata, "The plugin request could not be completed."
            )
        except Exception:
            pass
        raise
