# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Owner-only notes API compatibility implementation."""

import json
import os
import time
import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from open_webui.env import DATABASE_URL
from open_webui.models.groups import Groups
from open_webui.models.notes import PrivateNotes, PrivateNotesUnavailable
from open_webui.utils.auth import get_verified_user


router = APIRouter()


def _load_notes_store(database_url):
    try:
        return PrivateNotes(database_url)
    except PrivateNotesUnavailable:
        return None


notes_store = (
    _load_notes_store(DATABASE_URL)
    if os.environ.get("OPENATELIER_PRIVATE_NOTES_COMPAT", "1") != "0"
    else None
)


class NoteForm(BaseModel):
    title: str
    data: Optional[dict] = None
    meta: Optional[dict] = None
    access_grants: Optional[List[dict]] = None


class NoteOwner(BaseModel):
    id: str
    name: str
    role: str
    email: str


class AccessGrantModel(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    principal_type: str
    principal_id: str
    permission: str
    created_at: int


class NoteModel(BaseModel):
    id: str
    user_id: str
    title: str
    created_at: int
    updated_at: int
    data: Optional[dict] = None
    meta: Optional[dict] = None
    access_grants: List[AccessGrantModel] = Field(default_factory=list)
    is_pinned: Optional[bool] = None


class NoteItemResponse(BaseModel):
    id: str
    title: str
    data: Optional[dict]
    updated_at: int
    created_at: int
    is_pinned: Optional[bool] = None
    user: Optional[NoteOwner] = None


class NoteUserResponse(NoteModel):
    user: Optional[NoteOwner] = None


class NoteResponse(NoteModel):
    write_access: bool = False


class NoteListResponse(BaseModel):
    items: list[NoteUserResponse]
    total: int


class NoteAccessGrantsForm(BaseModel):
    access_grants: list[dict]


class NotePageOperation(BaseModel):
    action: Literal["create", "rename", "duplicate", "reorder", "move", "delete"]
    revision: int = Field(ge=0)
    page_id: Optional[str] = None
    new_id: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=200)
    parent_id: Optional[str] = None
    before_id: Optional[str] = None
    order: Optional[list[str]] = None


class NoteChatResponse(BaseModel):
    id: str
    user_id: str
    title: str
    chat: dict
    updated_at: int
    created_at: int
    share_id: Optional[str] = None
    archived: bool = False
    pinned: Optional[bool] = False
    meta: dict = Field(default_factory=dict)
    folder_id: Optional[str] = None
    variables: dict = Field(default_factory=dict)
    tasks: Optional[list] = None
    summary: Optional[str] = None
    current_message_id: Optional[str] = None
    context_usage: Optional[dict] = None


def _explicitly_disabled(permissions):
    permissions = getattr(permissions, "value", permissions)
    for section in ("workspace", "features"):
        group = permissions.get(section) if isinstance(permissions, dict) else getattr(permissions, section, None)
        value = group.get("notes") if isinstance(group, dict) else getattr(group, "notes", None)
        if value is False:
            return True
    return False


def _allow_notes(request, user):
    if notes_store is None:
        raise HTTPException(status_code=503, detail="Private notes unavailable")
    config = getattr(request.app.state, "config", None)
    if _explicitly_disabled(getattr(config, "USER_PERMISSIONS", None)):
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        groups = Groups.get_groups_by_member_id(user.id)
    except Exception:
        raise HTTPException(status_code=503, detail="Private notes unavailable") from None
    if any(_explicitly_disabled(group.permissions) for group in groups):
        raise HTTPException(status_code=403, detail="Access denied")


def _json_text(value):
    def check(item, depth=0):
        if depth > 64:
            raise ValueError("JSON nesting limit exceeded")
        if item is None or type(item) in (str, bool, int):
            if isinstance(item, str):
                item.encode("utf-8")
            return
        if type(item) is float:
            if item != item or item in (float("inf"), float("-inf")):
                raise ValueError("Non-finite number")
            return
        if isinstance(item, list):
            for child in item:
                check(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("Invalid JSON object key")
                key.encode("utf-8")
                check(child, depth + 1)
            return
        raise ValueError("Unsupported JSON value")

    check(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _search_text(title, pages):
    values = [title]
    for page in pages:
        if isinstance(page, dict):
            values.append(str(page.get("title", "")))
            content = page.get("content")
            if isinstance(content, dict) and isinstance(content.get("md"), str):
                values.append(content["md"])
    return "\n".join(value for value in values if value)


def _page_data(title, value):
    data = dict(value) if isinstance(value, dict) else {}
    if isinstance(data.get("pages"), list) and type(data.get("pages_revision")) is int:
        return data
    content = data.get("content") if isinstance(data.get("content"), dict) else {}
    pages = [{
        "id": "main", "title": title, "content": content, "parent_id": None,
        "revision": 0, "generation": 0, "versions": [],
    }]
    data["pages"] = pages
    data["pages_revision"] = 0
    data["search_text"] = _search_text(title, pages)
    return data


def _form_values(form):
    try:
        if len(form.title.encode("utf-8")) > 8192:
            raise HTTPException(status_code=422, detail="Note exceeds the supported size")
        normalized_data = (
            _page_data(form.title, form.data)
            if isinstance(form.data, dict) and "content" in form.data
            else form.data
        )
        data = _json_text(normalized_data) if normalized_data is not None else None
        meta = _json_text(form.meta) if form.meta is not None else None
        body = _json_text({"title": form.title, "data": normalized_data, "meta": form.meta})
        if len(body.encode("utf-8")) > 32768:
            raise ValueError("Note exceeds the supported size")
    except HTTPException:
        raise
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise HTTPException(status_code=422, detail="Note contains unsupported data") from None
    return data, meta


def _unavailable(error):
    if isinstance(error, PrivateNotesUnavailable):
        return HTTPException(status_code=503, detail="Private notes unavailable")
    return HTTPException(status_code=503, detail="Private notes unavailable")


def _page(value):
    if value is None:
        return 1
    if value < 1 or value > 4096:
        raise HTTPException(status_code=422, detail="Invalid page")
    return value


def _group_ids(user_id):
    try:
        return [group.id for group in Groups.get_groups_by_member_id(user_id)]
    except Exception:
        raise HTTPException(status_code=503, detail="Private notes unavailable") from None


def _with_grants(note):
    if note is None:
        return None
    result = dict(note)
    try:
        result["access_grants"] = notes_store.grants(note["id"])
    except Exception as error:
        raise _unavailable(error) from None
    return result


def _get_note_for(user, note_id, permission="read"):
    try:
        note = notes_store.get(note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="We could not find what you're looking for :/")
        if user.role == "admin" or note["user_id"] == user.id:
            return _with_grants(note)
        if notes_store.has_grant(note_id, user.id, permission, _group_ids(user.id)):
            return _with_grants(note)
    except HTTPException:
        raise
    except Exception as error:
        raise _unavailable(error) from None
    raise HTTPException(status_code=403, detail="Access denied")


def _normalized_grants(note_id, grants):
    if not isinstance(grants, list):
        raise HTTPException(status_code=422, detail="Invalid access grants")
    now = int(time.time())
    result, seen = [], set()
    for grant in grants:
        if not isinstance(grant, dict):
            raise HTTPException(status_code=422, detail="Invalid access grant")
        principal_type = grant.get("principal_type")
        principal_id = grant.get("principal_id")
        permission = grant.get("permission")
        if permission not in ("read", "write"):
            continue
        if principal_type not in ("user", "group") or not isinstance(principal_id, str) or not principal_id:
            raise HTTPException(status_code=422, detail="Invalid access grant")
        identity = (principal_type, principal_id, permission)
        if identity in seen:
            continue
        seen.add(identity)
        result.append({
            "id": str(uuid.uuid4()), "resource_type": "note", "resource_id": note_id,
            "principal_type": principal_type, "principal_id": principal_id,
            "permission": permission, "created_at": now,
        })
    return result


def _chat_response(chat):
    values = chat.model_dump()
    payload = values.get("chat") or {}
    history = payload.get("history") if isinstance(payload, dict) else None
    values.update({
        "variables": {}, "tasks": None, "summary": None, "context_usage": None,
        "current_message_id": history.get("currentId") if isinstance(history, dict) else None,
    })
    values.setdefault("folder_id", None)
    return values


def _create_note_chat(note_id, user):
    from open_webui.models.chats import Chats

    chat = Chats.insert_note_chat(user.id, note_id)
    if chat is None:
        raise HTTPException(status_code=503, detail="Private notes unavailable")
    try:
        if not notes_store.add_chat(note_id, chat.id, user.id, time.time_ns()):
            Chats.delete_chat_by_id_and_user_id(chat.id, user.id)
            raise HTTPException(status_code=404, detail="We could not find what you're looking for :/")
    except HTTPException:
        raise
    except Exception as error:
        Chats.delete_chat_by_id_and_user_id(chat.id, user.id)
        raise _unavailable(error) from None
    return _chat_response(chat)


def _note_chats(note_id, user):
    from open_webui.models.chats import Chats

    try:
        chat_ids = notes_store.chat_ids(note_id, user.id)
    except Exception as error:
        raise _unavailable(error) from None
    result = []
    for chat_id in chat_ids:
        chat = Chats.get_chat_by_id_and_user_id(chat_id, user.id)
        if chat is not None:
            result.append(_chat_response(chat))
    return result


@router.get("/", response_model=list[NoteItemResponse])
def list_notes(request: Request, page: Optional[int] = Query(default=1), user=Depends(get_verified_user)):
    _allow_notes(request, user)
    page = _page(page)
    try:
        rows, _ = notes_store.list_owned(user.id, 32, (page - 1) * 32)
        return rows
    except Exception as error:
        raise _unavailable(error) from None


@router.get("/pinned", response_model=list[NoteItemResponse])
def list_pinned_notes(request: Request, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    try:
        rows, _ = notes_store.list_owned(user.id, 32, 0, pinned=True)
        return rows
    except Exception as error:
        raise _unavailable(error) from None


@router.get(
    "/search",
    response_model=NoteListResponse,
    operation_id="search_notes_api_v1_notes_search_get",
)
def query_private_notes(
    request: Request,
    query: Optional[str] = None,
    view_option: Optional[str] = None,
    permission: Optional[str] = None,
    order_by: Optional[str] = None,
    direction: Optional[str] = None,
    page: Optional[int] = Query(default=1),
    user=Depends(get_verified_user),
):
    _allow_notes(request, user)
    if any(value not in (None, "") for value in (view_option, permission, order_by, direction)):
        raise HTTPException(status_code=422, detail="Unsupported private notes search option")
    page = _page(page)
    try:
        if query is not None and len(query.encode("utf-8")) > 256:
            raise HTTPException(status_code=422, detail="Search query is too long")
    except UnicodeError:
        raise HTTPException(status_code=422, detail="Unsupported search query") from None
    try:
        rows, total = notes_store.list_owned(user.id, 32, (page - 1) * 32, query=query or None)
        profile = {key: getattr(user, key) for key in ("id", "name", "role", "email")}
        rows = [{**row, "user": profile} for row in rows]
        return {"items": rows, "total": total}
    except Exception as error:
        raise _unavailable(error) from None


@router.post("/create", response_model=NoteModel)
def create_note(request: Request, form_data: NoteForm, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    data, meta = _form_values(form_data)
    now = time.time_ns()
    note = {"id": str(uuid.uuid4()), "user_id": user.id, "title": form_data.title,
            "data": data, "meta": meta, "created_at": now, "updated_at": now}
    try:
        created = notes_store.create(note)
        grants = _normalized_grants(created["id"], form_data.access_grants or [])
        if grants:
            notes_store.replace_grants(created["id"], grants)
        return _with_grants(created)
    except Exception as error:
        raise _unavailable(error) from None


@router.get("/{id}", response_model=NoteResponse)
def get_note(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    note = _get_note_for(user, id, "read")
    note["write_access"] = user.role == "admin" or note["user_id"] == user.id
    return note


@router.post("/{id}/update", response_model=NoteModel)
def update_note(request: Request, id: str, form_data: NoteForm, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    existing = _get_note_for(user, id, "write")
    data, meta = _form_values(form_data)
    try:
        note = notes_store.update_owned(id, existing["user_id"], form_data.title, data, meta, time.time_ns(),
                                        update_data=bool(form_data.data), update_meta=bool(form_data.meta))
    except ValueError:
        raise HTTPException(status_code=422, detail="Note exceeds the supported size or format") from None
    except Exception as error:
        raise _unavailable(error) from None
    if note is None:
        raise HTTPException(status_code=403, detail="Access denied")
    return _with_grants(note)


@router.post("/{id}/pin", response_model=NoteModel)
def pin_note(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    try:
        note = notes_store.toggle_pin(id, user.id)
    except Exception as error:
        raise _unavailable(error) from None
    if note is None:
        raise HTTPException(status_code=403, detail="Access denied")
    return note


@router.get("/{id}/chat", response_model=NoteChatResponse)
def get_note_chat(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    _get_note_for(user, id, "read")
    chats = _note_chats(id, user)
    return chats[0] if chats else _create_note_chat(id, user)


@router.get("/{id}/chats", response_model=list[NoteChatResponse])
def get_note_chats(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    _get_note_for(user, id, "read")
    return _note_chats(id, user)


@router.post("/{id}/chat", response_model=NoteChatResponse)
def create_note_chat(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    _get_note_for(user, id, "read")
    return _create_note_chat(id, user)


@router.post("/{id}/access/update", response_model=NoteModel)
def update_note_access(
    request: Request,
    id: str,
    form_data: NoteAccessGrantsForm,
    user=Depends(get_verified_user),
):
    _allow_notes(request, user)
    try:
        note = notes_store.get(id)
    except Exception as error:
        raise _unavailable(error) from None
    if note is None:
        raise HTTPException(status_code=404, detail="We could not find what you're looking for :/")
    if user.role != "admin" and note["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    grants = _normalized_grants(id, form_data.access_grants)
    try:
        if notes_store.replace_grants(id, grants) is None:
            raise HTTPException(status_code=404, detail="We could not find what you're looking for :/")
    except HTTPException:
        raise
    except Exception as error:
        raise _unavailable(error) from None
    return _with_grants(note)


@router.delete("/{id}/delete", response_model=bool)
def delete_note(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    try:
        note = notes_store.get(id)
    except Exception as error:
        raise _unavailable(error) from None
    if note is None:
        raise HTTPException(status_code=404, detail="We could not find what you're looking for :/")
    if user.role != "admin" and note["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    try:
        if not notes_store.delete(id, note["user_id"]):
            raise HTTPException(status_code=404, detail="We could not find what you're looking for :/")
    except HTTPException:
        raise
    except Exception as error:
        raise _unavailable(error) from None
    return True


def _page_id(value):
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="Invalid page id")
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="badly formed hexadecimal UUID string") from None
    return value


def _find_page(pages, page_id):
    return next((page for page in pages if page.get("id") == page_id), None)


def _apply_page_operation(note, operation):
    data = _page_data(note["title"], note.get("data"))
    pages = [dict(page) for page in data["pages"] if isinstance(page, dict)]
    page_ids = [page.get("id") for page in pages]
    action = operation.action
    if action == "create":
        new_id = _page_id(operation.new_id or str(uuid.uuid4()))
        if new_id in page_ids:
            raise HTTPException(status_code=400, detail="Page already exists")
        parent_id = operation.parent_id
        if parent_id is not None and parent_id not in page_ids:
            raise HTTPException(status_code=400, detail="Parent page not found")
        new_page = {
            "id": new_id, "title": operation.title or "Untitled", "content": {"md": ""},
            "parent_id": parent_id, "revision": 0, "generation": 0, "versions": [],
        }
        if operation.before_id is None:
            pages.append(new_page)
        else:
            index = next((index for index, page in enumerate(pages) if page.get("id") == operation.before_id), None)
            if index is None:
                raise HTTPException(status_code=400, detail="Reference page not found")
            pages.insert(index, new_page)
    elif action == "rename":
        page = _find_page(pages, operation.page_id)
        if page is None or operation.title is None:
            raise HTTPException(status_code=400, detail="Page not found")
        page["title"] = operation.title
    elif action == "duplicate":
        page = _find_page(pages, operation.page_id)
        if page is None:
            raise HTTPException(status_code=400, detail="Page not found")
        new_id = _page_id(operation.new_id or str(uuid.uuid4()))
        if new_id in page_ids:
            raise HTTPException(status_code=400, detail="Page already exists")
        copied = json.loads(json.dumps(page))
        copied["id"] = new_id
        copied["title"] = str(page.get("title", "Untitled")) + " (Copy)"
        pages.insert(pages.index(page) + 1, copied)
    elif action == "reorder":
        if operation.order is None or len(operation.order) != len(pages) or set(operation.order) != set(page_ids):
            raise HTTPException(status_code=400, detail="Invalid page order")
        by_id = {page["id"]: page for page in pages}
        pages = [by_id[page_id] for page_id in operation.order]
    elif action == "move":
        page = _find_page(pages, operation.page_id)
        if page is None:
            raise HTTPException(status_code=400, detail="Page not found")
        parent_id = operation.parent_id
        if parent_id is not None and _find_page(pages, parent_id) is None:
            raise HTTPException(status_code=400, detail="Parent page not found")
        ancestor = parent_id
        while ancestor is not None:
            if ancestor == page.get("id"):
                raise HTTPException(status_code=400, detail="Invalid page hierarchy")
            parent = _find_page(pages, ancestor)
            ancestor = parent.get("parent_id") if parent else None
        page["parent_id"] = parent_id
        if operation.before_id is not None:
            before = _find_page(pages, operation.before_id)
            if before is None:
                raise HTTPException(status_code=400, detail="Reference page not found")
            pages.remove(page)
            pages.insert(pages.index(before), page)
    elif action == "delete":
        page = _find_page(pages, operation.page_id)
        if page is None or page.get("id") == "main":
            raise HTTPException(status_code=400, detail="Page not found")
        removed = {page.get("id")}
        changed = True
        while changed:
            changed = False
            for candidate in pages:
                if candidate.get("parent_id") in removed and candidate.get("id") not in removed:
                    removed.add(candidate.get("id"))
                    changed = True
        pages = [candidate for candidate in pages if candidate.get("id") not in removed]
    data["pages"] = pages
    data["pages_revision"] = operation.revision + 1
    data["search_text"] = _search_text(note["title"], pages)
    return data


@router.post("/{id}/pages/update", response_model=NoteModel)
def update_note_pages(
    request: Request,
    id: str,
    form_data: NotePageOperation,
    user=Depends(get_verified_user),
):
    _allow_notes(request, user)
    note = _get_note_for(user, id, "write")
    current = _page_data(note["title"], note.get("data"))
    if current.get("pages_revision", 0) != form_data.revision:
        raise HTTPException(status_code=409, detail="Pages changed. Refresh and try again")
    updated_data = _apply_page_operation(note, form_data)
    try:
        updated, conflict = notes_store.replace_pages(
            id, note["user_id"], form_data.revision, updated_data, time.time_ns()
        )
    except Exception as error:
        raise _unavailable(error) from None
    if conflict:
        raise HTTPException(status_code=409, detail="Pages changed. Refresh and try again")
    if updated is None:
        raise HTTPException(status_code=404, detail="We could not find what you're looking for :/")
    return _with_grants(updated)
