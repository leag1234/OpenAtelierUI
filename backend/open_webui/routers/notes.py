# SPDX-License-Identifier: BSD-3-Clause
"""Default-off, owner-only private notes API compatibility prototype."""

import json
import os
import time
import uuid
from typing import List, Optional

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
    if os.environ.get("OPENATELIER_PRIVATE_NOTES_COMPAT") == "1"
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


class NoteModel(BaseModel):
    id: str
    user_id: str
    title: str
    created_at: int
    updated_at: int
    data: Optional[dict] = None
    meta: Optional[dict] = None
    access_grants: List[dict] = Field(default_factory=list)
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


def _form_values(form):
    if form.access_grants not in (None, []):
        raise HTTPException(status_code=422, detail="Private notes do not support sharing")
    try:
        if len(form.title.encode("utf-8")) > 8192:
            raise HTTPException(status_code=422, detail="Note exceeds the supported size")
        data = _json_text(form.data) if form.data is not None else None
        meta = _json_text(form.meta) if form.meta is not None else None
        body = _json_text({"title": form.title, "data": form.data, "meta": form.meta})
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


@router.get("/search", response_model=NoteListResponse)
def search_notes(
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
        return notes_store.create(note)
    except Exception as error:
        raise _unavailable(error) from None


@router.get("/{id}", response_model=NoteResponse)
def get_note(request: Request, id: str, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    try:
        note = notes_store.get_owned(id, user.id)
    except Exception as error:
        raise _unavailable(error) from None
    if note is None:
        raise HTTPException(status_code=403, detail="Access denied")
    note["write_access"] = True
    return note


@router.post("/{id}/update", response_model=NoteModel)
def update_note(request: Request, id: str, form_data: NoteForm, user=Depends(get_verified_user)):
    _allow_notes(request, user)
    data, meta = _form_values(form_data)
    try:
        note = notes_store.update_owned(id, user.id, form_data.title, data, meta, time.time_ns())
    except Exception as error:
        raise _unavailable(error) from None
    if note is None:
        raise HTTPException(status_code=403, detail="Access denied")
    return note


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
