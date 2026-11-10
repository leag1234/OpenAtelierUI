# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Measured global-search compatibility for chats, channels, and private notes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote, quote_plus

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from open_webui.models.channels import ChannelCompat, Channels
from open_webui.models.chats import Chats
from open_webui.models.folders import Folders
from open_webui.models.messages import Messages
from open_webui.models.users import Users
from open_webui.routers.notes import _allow_notes, _group_ids, _page_data, notes_store
from open_webui.utils.auth import get_verified_user


router = APIRouter()

SearchKind = Literal["chat", "message", "note"]
SearchQueryKind = Literal["all", "chat", "message", "note"]
SearchSharing = Literal["all", "mine", "shared"]
SearchScopeKind = Literal["folder", "channel"]

PAGE_SIZE = 24
# Each backing store is bounded independently. Ordinary searches stop after the first
# 100 result pages instead of materialising an arbitrarily large workspace in memory.
MAX_DOMAIN_CANDIDATES = PAGE_SIZE * 100


class SearchScope(BaseModel):
    id: str
    name: str
    kind: SearchScopeKind


class SearchResult(BaseModel):
    id: str
    kind: SearchKind
    title: str
    context: str
    snippet: str
    preview: str
    url: str
    updated_at: int
    pinned: bool = False
    shared: bool = False


class SearchResponse(BaseModel):
    items: list[SearchResult]
    has_more: bool
    scopes: list[SearchScope]
    kinds: list[str]


@dataclass
class _Candidate:
    result: SearchResult
    owned: bool
    scope: str | None


def _tokens(query: str) -> list[str]:
    return [part.casefold() for part in query.split() if part]


def _matches(value: str, tokens: list[str]) -> bool:
    folded = value.casefold()
    return all(token in folded for token in tokens)


def _seconds(value: Any) -> int:
    timestamp = value if type(value) is int else 0
    return timestamp // 1_000_000_000 if timestamp > 10_000_000_000 else timestamp


def _chat_messages(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    history = payload.get("history")
    if isinstance(history, dict) and isinstance(history.get("messages"), dict):
        messages = list(history["messages"].values())
    elif isinstance(payload.get("messages"), list):
        messages = payload["messages"]
    else:
        messages = []
    return [message for message in messages if isinstance(message, dict)]


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _selected_message(messages: list[dict[str, Any]], tokens: list[str]) -> dict[str, Any] | None:
    if tokens:
        for message in messages:
            if any(token in _message_text(message).casefold() for token in tokens):
                return message
    return messages[-1] if messages else None


def _notes_available(request: Request, user: Any) -> bool:
    try:
        _allow_notes(request, user)
        return notes_store is not None
    except Exception:
        return False


def _accessible_channels(user: Any) -> list[Any]:
    identifiers = list(
        dict.fromkeys(
            [
                *ChannelCompat.channel_ids(user.id),
                *(channel.id for channel in Channels.get_channels_by_user_id(user.id)),
            ]
        )
    )
    return [
        channel
        for identifier in identifiers[:MAX_DOMAIN_CANDIDATES]
        if (channel := Channels.get_channel_by_id(identifier)) is not None
    ]


def _scopes(
    user: Any, channels: list[Any]
) -> tuple[list[SearchScope], dict[str, str], list[Any]]:
    folders = {folder.id: folder for folder in Folders.get_folders_by_user_id(user.id)}
    shared_folders = []
    try:
        for folder, _permission in Folders.get_shared_folders_by_user_id(user.id, include_owned=False):
            folders[folder.id] = folder
            shared_folders.append(folder)
    except Exception:
        pass
    scopes = [
        SearchScope(id=f"channel:{channel.id}", name=channel.name, kind="channel")
        for channel in channels
    ]
    scopes.extend(
        SearchScope(id=f"folder:{folder.id}", name=folder.name, kind="folder")
        for folder in folders.values()
    )
    scopes.sort(key=lambda item: (0 if item.kind == "channel" else 1, item.name.casefold(), item.id))
    return (
        scopes,
        {identifier: folder.name for identifier, folder in folders.items()},
        shared_folders,
    )


def _chat_candidates(
    user: Any,
    tokens: list[str],
    folder_names: dict[str, str],
    shared_folders: list[Any],
) -> list[_Candidate]:
    rows = [(chat, True) for chat in Chats.get_chats_by_user_id(user.id)]
    for folder in shared_folders:
        rows.extend(
            (chat, False)
            for chat in Chats.get_chats_by_folder_id_and_user_id(folder.id, folder.user_id)
        )
    unique_rows = {}
    for chat, owned in rows:
        unique_rows.setdefault(chat.id, (chat, owned))
    candidates: list[_Candidate] = []
    for chat, owned in list(unique_rows.values())[:MAX_DOMAIN_CANDIDATES]:
        messages = _chat_messages(chat.chat)
        searchable = "\n".join([chat.title, *(_message_text(message) for message in messages)])
        if tokens and not _matches(searchable, tokens):
            continue
        selected = _selected_message(messages, tokens)
        snippet = _message_text(selected) if selected else chat.title
        message_id = selected.get("id") if selected else None
        suffix = f"?message={quote(message_id, safe='')}" if tokens and isinstance(message_id, str) else ""
        candidates.append(
            _Candidate(
                SearchResult(
                    id=f"chat:{chat.id}",
                    kind="chat",
                    title=chat.title,
                    context=folder_names.get(chat.folder_id, "Personal chat"),
                    snippet=snippet,
                    preview=snippet,
                    url=f"/c/{chat.id}{suffix}",
                    updated_at=_seconds(chat.updated_at),
                    pinned=bool(chat.pinned),
                    shared=not owned or bool(chat.share_id),
                ),
                owned=owned,
                scope=f"folder:{chat.folder_id}" if chat.folder_id else None,
            )
        )
    return candidates


def _note_markdown(note: dict[str, Any]) -> str:
    data = _page_data(note["title"], note.get("data"))
    pages = data.get("pages") if isinstance(data, dict) else None
    main = pages[0] if isinstance(pages, list) and pages and isinstance(pages[0], dict) else {}
    content = main.get("content") if isinstance(main.get("content"), dict) else {}
    markdown = content.get("md")
    return markdown if isinstance(markdown, str) else ""


def _note_candidates(user: Any, query: str, tokens: list[str]) -> list[_Candidate]:
    try:
        rows, _total = notes_store.list_accessible(
            user.id,
            _group_ids(user.id),
            MAX_DOMAIN_CANDIDATES,
            0,
            permissions=("read",),
            include_owned=True,
            admin=False,
        )
    except Exception:
        return []
    candidates: list[_Candidate] = []
    for note in rows:
        markdown = _note_markdown(note)
        if tokens and not _matches(f"{note['title']}\n{markdown}", tokens):
            continue
        owned = note.get("user_id") == user.id
        suffix = f"&search={quote_plus(query, safe='')}" if query else ""
        candidates.append(
            _Candidate(
                SearchResult(
                    id=f"note:{note['id']}",
                    kind="note",
                    title=note["title"],
                    context="Personal note" if owned else "Shared note",
                    snippet=markdown or note["title"],
                    preview=markdown or note["title"],
                    url=f"/notes/{note['id']}?page=main{suffix}",
                    updated_at=_seconds(note.get("updated_at")),
                    pinned=bool(note.get("actor_pinned", note.get("is_pinned"))),
                    shared=not owned,
                ),
                owned=owned,
                scope=None,
            )
        )
    return candidates


def _message_candidates(user: Any, channels: list[Any], tokens: list[str]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for channel in channels:
        messages = Messages.get_messages_by_channel_id(channel.id, 0, MAX_DOMAIN_CANDIDATES)
        for message in messages:
            if tokens and not _matches(message.content, tokens):
                continue
            author = Users.get_user_by_id(message.user_id)
            title = author.email if author is not None else message.user_id
            candidates.append(
                _Candidate(
                    SearchResult(
                        id=f"message:{message.id}",
                        kind="message",
                        title=title,
                        context=f"#{channel.name}",
                        snippet=message.content,
                        preview=message.content,
                        url=f"/channels/{channel.id}?thread={message.id}&message={message.id}",
                        updated_at=_seconds(message.updated_at),
                        shared=True,
                    ),
                    owned=message.user_id == user.id,
                    scope=f"channel:{channel.id}",
                )
            )
    return candidates


@router.get("", response_model=SearchResponse)
def search(
    request: Request,
    q: str = Query(default="", max_length=500),
    kind: SearchQueryKind = Query(default="all"),
    sharing: SearchSharing = Query(default="all"),
    scope: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1, le=100000),
    user=Depends(get_verified_user),
):
    query = q.strip()
    tokens = _tokens(query)
    channels_enabled = bool(getattr(request.app.state.config, "ENABLE_CHANNELS", False))
    notes_enabled = _notes_available(request, user)
    channels = _accessible_channels(user) if channels_enabled else []
    scopes, folder_names, shared_folders = _scopes(user, channels)
    kinds = ["chat", *(["message"] if channels_enabled else []), *(["note"] if notes_enabled else [])]

    candidates: list[_Candidate] = []
    if kind in ("all", "chat"):
        candidates.extend(_chat_candidates(user, tokens, folder_names, shared_folders))
    if channels_enabled and kind in ("all", "message"):
        candidates.extend(_message_candidates(user, channels, tokens))
    if notes_enabled and kind in ("all", "note"):
        candidates.extend(_note_candidates(user, query, tokens))

    if scope:
        candidates = [candidate for candidate in candidates if candidate.scope == scope]
    if sharing == "mine":
        candidates = [candidate for candidate in candidates if candidate.owned]
    elif sharing == "shared":
        candidates = [candidate for candidate in candidates if not candidate.owned]

    candidates.sort(key=lambda candidate: (-candidate.result.updated_at, candidate.result.id))
    offset = (page - 1) * PAGE_SIZE
    selected = candidates[offset : offset + PAGE_SIZE]
    return SearchResponse(
        items=[candidate.result for candidate in selected],
        has_more=len(candidates) > offset + PAGE_SIZE,
        scopes=scopes,
        kinds=kinds,
    )
