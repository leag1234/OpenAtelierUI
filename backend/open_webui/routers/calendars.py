# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Measured local Calendar API compatibility implementation."""

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from open_webui.env import DATABASE_URL
from open_webui.models.calendars import Calendars, CalendarsUnavailable
from open_webui.models.groups import Groups
from open_webui.models.users import Users
from open_webui.utils.auth import get_verified_user


router = APIRouter()


def _load_store(database_url):
    try:
        return Calendars(database_url)
    except CalendarsUnavailable:
        return None


calendar_store = (
    _load_store(DATABASE_URL)
    if os.environ.get("OPENATELIER_CALENDAR_COMPAT", "1") != "0"
    else None
)


class AccessGrantModel(BaseModel):
    id: str
    resource_type: str
    resource_id: str
    principal_type: str
    principal_id: str
    permission: str
    created_at: int


class CalendarForm(BaseModel):
    name: str
    color: Optional[str] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    access_grants: Optional[list[dict]] = None


class CalendarUpdateForm(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    access_grants: Optional[list[dict]] = None


class CalendarModel(BaseModel):
    id: str
    user_id: str
    name: str
    color: Optional[str] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    access_grants: list[AccessGrantModel] = Field(default_factory=list)
    is_default: bool = False
    is_system: bool = False
    created_at: int
    updated_at: int


class CalendarEventAttendeeModel(BaseModel):
    id: str
    event_id: str
    user_id: str
    status: str = "pending"
    meta: Optional[dict] = None
    created_at: int
    updated_at: int


class CalendarEventForm(BaseModel):
    calendar_id: str
    title: str
    start_at: int
    end_at: Optional[int] = None
    all_day: bool = False
    description: Optional[str] = None
    location: Optional[str] = None
    color: Optional[str] = None
    rrule: Optional[str] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    attendees: Optional[list[dict]] = None


class CalendarEventUpdateForm(BaseModel):
    calendar_id: Optional[str] = None
    title: Optional[str] = None
    start_at: Optional[int] = None
    end_at: Optional[int] = None
    all_day: Optional[bool] = None
    description: Optional[str] = None
    location: Optional[str] = None
    color: Optional[str] = None
    rrule: Optional[str] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    attendees: Optional[list[dict]] = None
    is_cancelled: Optional[bool] = None


class CalendarUserResponse(BaseModel):
    id: str
    name: str
    role: str
    email: str


class CalendarEventModel(BaseModel):
    id: str
    calendar_id: str
    user_id: str
    title: str
    start_at: int
    created_at: int
    updated_at: int
    end_at: Optional[int] = None
    all_day: bool = False
    description: Optional[str] = None
    location: Optional[str] = None
    color: Optional[str] = None
    rrule: Optional[str] = None
    data: Optional[dict] = None
    meta: Optional[dict] = None
    is_cancelled: bool = False
    attendees: list[CalendarEventAttendeeModel] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow")


class CalendarEventUserResponse(CalendarEventModel):
    user: Optional[CalendarUserResponse] = None


class CalendarEventListResponse(BaseModel):
    items: list[CalendarEventUserResponse]
    total: int


class RSVPForm(BaseModel):
    status: str


def _explicitly_disabled(permissions):
    permissions = getattr(permissions, "value", permissions)
    for section in ("workspace", "features"):
        group = permissions.get(section) if isinstance(permissions, dict) else getattr(permissions, section, None)
        value = group.get("calendar") if isinstance(group, dict) else getattr(group, "calendar", None)
        if value is False:
            return True
    return False


def _groups(user):
    try:
        return Groups.get_groups_by_member_id(user.id)
    except Exception:
        raise HTTPException(status_code=503, detail="Calendar unavailable") from None


def _allow_calendar(request, user):
    if calendar_store is None:
        raise HTTPException(status_code=503, detail="Calendar unavailable")
    config = getattr(request.app.state, "config", None)
    if _explicitly_disabled(getattr(config, "USER_PERMISSIONS", None)):
        raise HTTPException(status_code=403, detail="Access denied")
    groups = _groups(user)
    if any(_explicitly_disabled(group.permissions) for group in groups):
        raise HTTPException(status_code=403, detail="Access denied")
    return [group.id for group in groups]


def _json_text(value):
    if value is None:
        return None
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 65536:
            raise ValueError
        return encoded
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise HTTPException(status_code=422, detail="Calendar contains unsupported data") from None


def _grants(value):
    result, seen = [], set()
    for grant in value or []:
        if not isinstance(grant, dict):
            raise HTTPException(status_code=422, detail="Invalid calendar access grant")
        principal_type = grant.get("principal_type")
        principal_id = grant.get("principal_id")
        permission = grant.get("permission")
        identity = (principal_type, principal_id, permission)
        if (
            principal_type not in ("user", "group")
            or not isinstance(principal_id, str) or not principal_id
            or permission not in ("read", "write")
        ):
            raise HTTPException(status_code=422, detail="Invalid calendar access grant")
        if identity not in seen:
            result.append({"principal_type": principal_type, "principal_id": principal_id, "permission": permission})
            seen.add(identity)
    return result


def _attendees(value):
    result, seen = [], set()
    for attendee in value or []:
        if not isinstance(attendee, dict):
            raise HTTPException(status_code=422, detail="Invalid event attendee")
        user_id = attendee.get("user_id")
        status = attendee.get("status", "pending")
        meta = attendee.get("meta")
        if not isinstance(user_id, str) or not user_id or not isinstance(status, str) or not status:
            raise HTTPException(status_code=422, detail="Invalid event attendee")
        if meta is not None and not isinstance(meta, dict):
            raise HTTPException(status_code=422, detail="Invalid event attendee")
        if user_id not in seen:
            result.append({"user_id": user_id, "status": status, "meta": _json_text(meta)})
            seen.add(user_id)
    return result


def _calendar_for_read(calendar_id, user, group_ids):
    calendar = calendar_store.get(calendar_id)
    if calendar is None:
        raise HTTPException(status_code=404, detail="Calendar not found")
    if not calendar_store.can_read(calendar, user.id, group_ids, user.role == "admin"):
        raise HTTPException(status_code=403, detail="Access denied")
    return calendar


def _calendar_for_write(calendar_id, user, group_ids):
    calendar = calendar_store.get(calendar_id)
    if calendar is None:
        raise HTTPException(status_code=404, detail="Calendar not found")
    if not calendar_store.can_write(calendar, user.id, group_ids, user.role == "admin"):
        raise HTTPException(status_code=403, detail="Access denied")
    return calendar


def _event_for_read(event_id, user, group_ids):
    event = calendar_store.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    _calendar_for_read(event["calendar_id"], user, group_ids)
    return event


def _event_for_write(event_id, user, group_ids):
    event = calendar_store.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    _calendar_for_write(event["calendar_id"], user, group_ids)
    return event


def _event_user(event):
    value = dict(event)
    owner = Users.get_user_by_id(event["user_id"])
    value["user"] = (
        {"id": owner.id, "name": owner.name, "role": owner.role, "email": owner.email}
        if owner is not None else None
    )
    return value


def _parse_iso(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1_000_000_000)
    except (AttributeError, OverflowError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid date format. Use ISO 8601 (e.g. 2026-04-01T00:00:00)") from None


@router.get("/", response_model=list[CalendarModel])
def get_calendars(request: Request, user=Depends(get_verified_user)):
    """List user's calendars (owned + shared), plus a virtual Scheduled Tasks calendar
    when automations are available."""
    group_ids = _allow_calendar(request, user)
    calendar_store.ensure_personal(user.id)
    if user.role == "admin":
        calendar_store.ensure_system(user.id)
    return calendar_store.list_readable(user.id, group_ids)


@router.post("/create", response_model=CalendarModel)
def create_calendar(form: CalendarForm, request: Request, user=Depends(get_verified_user)):
    """Create a new user calendar."""
    _allow_calendar(request, user)
    return calendar_store.create(
        user.id, form.name, form.color, _json_text(form.data), _json_text(form.meta), _grants(form.access_grants)
    )


@router.get("/events")
def get_events(
    start: str,
    end: str,
    request: Request,
    calendar_ids: Optional[str] = None,
    user=Depends(get_verified_user),
):
    """Get events in date range.

    Args:
        start: ISO 8601 datetime string (e.g. 2026-04-01T00:00:00)
        end:   ISO 8601 datetime string (e.g. 2026-05-01T00:00:00)
        calendar_ids: optional comma-separated list to filter

    Includes:
    - Stored events from the database
    - Virtual events computed from active automation RRULEs (Scheduled Tasks calendar)
    """
    group_ids = _allow_calendar(request, user)
    start_at, end_at = _parse_iso(start), _parse_iso(end)
    if start_at > end_at:
        raise HTTPException(status_code=400, detail="Invalid date range")
    readable = calendar_store.list_readable(user.id, group_ids)
    allowed = {calendar["id"] for calendar in readable}
    requested = {value for value in (calendar_ids or "").split(",") if value}
    selected = sorted(allowed & requested) if requested else sorted(allowed)
    events, _ = calendar_store.list_events(selected, start_at=start_at, end_at=end_at)
    return [_event_user(event) for event in events]


@router.post("/events/create", response_model=CalendarEventModel)
def create_event(form: CalendarEventForm, request: Request, user=Depends(get_verified_user)):
    group_ids = _allow_calendar(request, user)
    _calendar_for_write(form.calendar_id, user, group_ids)
    values = form.model_dump(exclude={"attendees"})
    values["data"] = _json_text(form.data)
    values["meta"] = _json_text(form.meta)
    return calendar_store.create_event(user.id, values, _attendees(form.attendees))


@router.get("/events/search", response_model=CalendarEventListResponse)
def search_events(
    request: Request,
    query: Optional[str] = None,
    skip: int = Query(default=0),
    limit: int = Query(default=30),
    user=Depends(get_verified_user),
):
    group_ids = _allow_calendar(request, user)
    calendar_ids = [calendar["id"] for calendar in calendar_store.list_readable(user.id, group_ids)]
    events, total = calendar_store.list_events(calendar_ids, query=query, skip=skip, limit=limit)
    return {"items": [_event_user(event) for event in events], "total": total}


@router.get("/events/{event_id}", response_model=CalendarEventModel)
def get_event(event_id: str, request: Request, user=Depends(get_verified_user)):
    group_ids = _allow_calendar(request, user)
    return _event_for_read(event_id, user, group_ids)


@router.delete("/events/{event_id}/delete")
def delete_event(event_id: str, request: Request, user=Depends(get_verified_user)):
    group_ids = _allow_calendar(request, user)
    _event_for_write(event_id, user, group_ids)
    return {"status": calendar_store.delete_event(event_id)}


@router.post("/events/{event_id}/rsvp")
def rsvp_event(event_id: str, form: RSVPForm, request: Request, user=Depends(get_verified_user)) -> dict[str, Any]:
    """Update own RSVP status for an event."""
    _allow_calendar(request, user)
    if calendar_store.get_event(event_id) is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if not calendar_store.rsvp(event_id, user.id, form.status):
        raise HTTPException(status_code=404, detail="Not an attendee of this event")
    return {"status": True, "rsvp": form.status}


@router.post("/events/{event_id}/update", response_model=CalendarEventModel)
def update_event(event_id: str, form: CalendarEventUpdateForm, request: Request, user=Depends(get_verified_user)):
    group_ids = _allow_calendar(request, user)
    _event_for_write(event_id, user, group_ids)
    fields = set(form.model_fields_set)
    if "calendar_id" in fields and form.calendar_id is not None:
        _calendar_for_write(form.calendar_id, user, group_ids)
    values = form.model_dump(exclude={"attendees"}, exclude_unset=True)
    for name in ("data", "meta"):
        if name in fields:
            values[name] = _json_text(getattr(form, name))
    attendees = _attendees(form.attendees) if "attendees" in fields else None
    return calendar_store.update_event(event_id, values, attendees)


@router.get("/{calendar_id}", response_model=CalendarModel)
def get_calendar_by_id(calendar_id: str, request: Request, user=Depends(get_verified_user)):
    group_ids = _allow_calendar(request, user)
    return _calendar_for_read(calendar_id, user, group_ids)


@router.post("/{calendar_id}/default")
def set_default_calendar(calendar_id: str, request: Request, user=Depends(get_verified_user)):
    _allow_calendar(request, user)
    calendar = calendar_store.set_default(calendar_id, user.id)
    if calendar is None:
        raise HTTPException(status_code=404, detail="Calendar not found")
    return calendar


@router.delete("/{calendar_id}/delete")
def delete_calendar(calendar_id: str, request: Request, user=Depends(get_verified_user)):
    group_ids = _allow_calendar(request, user)
    _calendar_for_write(calendar_id, user, group_ids)
    return {"status": calendar_store.delete(calendar_id)}


@router.post("/{calendar_id}/update", response_model=CalendarModel)
def update_calendar(calendar_id: str, form: CalendarUpdateForm, request: Request, user=Depends(get_verified_user)):
    group_ids = _allow_calendar(request, user)
    _calendar_for_write(calendar_id, user, group_ids)
    fields = set(form.model_fields_set)
    values = {}
    if "name" in fields and form.name is not None:
        values["name"] = form.name
    for name in ("color", "data", "meta"):
        if name in fields:
            values[name] = _json_text(getattr(form, name)) if name in ("data", "meta") else getattr(form, name)
    grants = _grants(form.access_grants) if "access_grants" in fields else None
    return calendar_store.update(calendar_id, values, grants)
