# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Private Notes API compatibility implementation."""

import json
import os
import time
import uuid
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from open_webui.env import DATABASE_URL
from open_webui.models.chats import ChatResponse
from open_webui.models.groups import Groups
from open_webui.models.notes import PrivateNotes, PrivateNotesUnavailable
from open_webui.models.users import Users
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
    data: Optional[dict[str, Any]] = None
    meta: Optional[dict[str, Any]] = None
    access_grants: Optional[List[dict[str, Any]]] = None


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
    data: Optional[dict[str, Any]] = None
    meta: Optional[dict[str, Any]] = None
    access_grants: List[AccessGrantModel] = Field(default_factory=list)
    is_pinned: Optional[bool] = False


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
    access_grants: list[dict[str, Any]]


class NotePageOperation(BaseModel):
    action: Literal["create", "rename", "duplicate", "reorder", "move", "delete"]
    revision: int = Field(ge=0)
    page_id: Optional[str] = None
    new_id: Optional[str] = None
    title: Optional[str] = Field(default=None, max_length=200)
    parent_id: Optional[str] = None
    before_id: Optional[str] = None
    order: Optional[list[str]] = None


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
    if not pages:
        return title
    main = pages[0] if isinstance(pages[0], dict) else {}
    content = main.get("content") if isinstance(main.get("content"), dict) else {}
    markdown = content.get("md") if isinstance(content.get("md"), str) else ""
    return title + ("\n" + markdown if markdown else "")


def _rebuild_page_projection(pages):
    by_id = {page.get("id"): page for page in pages if isinstance(page.get("id"), str)}

    def path(page):
        titles, seen = [str(page.get("title", ""))], {page.get("id")}
        parent_id = page.get("parent_id")
        while parent_id is not None:
            if parent_id in seen or parent_id not in by_id:
                raise HTTPException(status_code=400, detail="Invalid page hierarchy")
            seen.add(parent_id)
            parent = by_id[parent_id]
            titles.append(str(parent.get("title", "")))
            parent_id = parent.get("parent_id")
        return " / ".join(reversed(titles))

    sections, searchable = [], []
    for page in pages:
        title = path(page)
        content = page.get("content") if isinstance(page.get("content"), dict) else {}
        markdown = content.get("md") if isinstance(content.get("md"), str) else ""
        sections.append("## " + title + "\n\n" + markdown)
        searchable.append(title + "\n" + markdown)
    return {
        "md": "\n\n".join(sections),
        "html": "",
        "json": None,
    }, "\n\n".join(searchable)


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


def _denied():
    return HTTPException(status_code=403, detail="Something went wrong :/")


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
    raise _denied()


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


def _owner_profile(user_id):
    owner = Users.get_user_by_id(user_id)
    if owner is None:
        return None
    return {key: getattr(owner, key) for key in ("id", "name", "role", "email")}


def _item_response(note):
    data = _page_data(note["title"], note.get("data"))
    pages = data.get("pages") or []
    main = pages[0] if pages and isinstance(pages[0], dict) else {}
    result = {
        key: note[key]
        for key in ("id", "title", "updated_at", "created_at", "is_pinned")
    }
    result["data"] = {
        "content": main.get("content") if isinstance(main.get("content"), dict) else {},
        "page_count": len(pages),
        "page_id": main.get("id"),
        "page_title": main.get("title"),
    }
    result["user"] = _owner_profile(note["user_id"])
    return result


def _search_response(note):
    result = _with_grants(note)
    result["data"] = _item_response(note)["data"]
    result["user"] = _owner_profile(note["user_id"])
    return result


@router.get("/", response_model=list[NoteItemResponse])
def list_notes(request: Request, page: Optional[int] = Query(default=1), user=Depends(get_verified_user)):
    _allow_notes(request, user)
    page = _page(page)
    try:
        rows, _ = notes_store.list_accessible(
            user.id, _group_ids(user.id), 32, (page - 1) * 32,
            permissions=("read",),
        )
        return [_item_response(row) for row in rows]
    except Exception as error:
        raise _unavailable(error) from None


@router.get("/pinned", response_model=list[NoteItemResponse])
def list_pinned_notes(request: Request, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    try:
        rows, _ = notes_store.list_accessible(
            user.id, _group_ids(user.id), 32, 0,
            permissions=("read",), pinned=True,
        )
        return [_item_response(row) for row in rows]
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
    page = _page(page)
    try:
        selected_permission = permission if permission in {"read", "write"} else (
            "write" if permission in (None, "") else None
        )
        rows, total = notes_store.list_accessible(
            user.id,
            _group_ids(user.id),
            32,
            (page - 1) * 32,
            permissions=(() if selected_permission is None else (selected_permission,)),
            include_owned=view_option != "shared",
            admin=user.role == "admin",
            query=query or None,
            order_by=order_by if order_by in {"created_at", "updated_at"} else "updated_at",
            direction=direction if order_by in {"created_at", "updated_at"} and direction in {"asc", "desc"} else "desc",
        )
        return {"items": [_search_response(row) for row in rows], "total": total}
    except Exception as error:
        raise _unavailable(error) from None


@router.post("/create", response_model=Optional[NoteModel])
def create_new_note(request: Request, form_data: NoteForm, user=Depends(get_verified_user)):
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
        raise _denied()
    return _with_grants(note)


@router.post("/{id}/pin", response_model=NoteModel)
def pin_note(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    try:
        note = notes_store.toggle_actor_pin(
            id,
            user.id,
            _group_ids(user.id),
            admin=user.role == "admin",
            created_at=int(time.time()),
        )
    except Exception as error:
        raise _unavailable(error) from None
    if note is None:
        raise _denied()
    return _with_grants(note)


@router.get("/{id}/chat", response_model=ChatResponse)
def get_note_chat_by_id(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    _get_note_for(user, id, "read")
    chats = _note_chats(id, user)
    return chats[0] if chats else _create_note_chat(id, user)


@router.get("/{id}/chats", response_model=list[ChatResponse])
def get_note_chats_by_id(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    _get_note_for(user, id, "read")
    return _note_chats(id, user)


@router.post("/{id}/chat", response_model=ChatResponse)
def create_note_chat_by_id(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    _get_note_for(user, id, "read")
    return _create_note_chat(id, user)


@router.post("/{id}/access/update", response_model=Optional[NoteModel])
def update_note_access_by_id(
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
        raise _denied()
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
def delete_note_by_id(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    try:
        note = notes_store.get(id)
    except Exception as error:
        raise _unavailable(error) from None
    if note is None:
        raise HTTPException(status_code=404, detail="We could not find what you're looking for :/")
    if user.role != "admin" and note["user_id"] != user.id:
        try:
            can_delete = notes_store.has_grant(
                id, user.id, "write", _group_ids(user.id)
            )
        except Exception as error:
            raise _unavailable(error) from None
        if not can_delete:
            raise _denied()
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
            "id": new_id, "title": operation.title or "Untitled",
            "content": {"md": "", "html": "", "json": None},
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
    data["content"], data["search_text"] = _rebuild_page_projection(pages)
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
