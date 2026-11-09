# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Narrow current-style projections for the measured flat-group REST bridge."""

from collections.abc import Mapping
from copy import deepcopy


MEMBER_PAGE_SIZE = 30


def _get(value, name, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def project_group_response(group):
    """Project old storage without exposing its embedded membership array."""
    user_ids = _get(group, "user_ids", [])
    if not isinstance(user_ids, list):
        user_ids = []
    exact_ids = list(dict.fromkeys(user_id for user_id in user_ids if isinstance(user_id, str)))
    return {
        "id": _get(group, "id"),
        "user_id": _get(group, "user_id"),
        "name": _get(group, "name"),
        "description": _get(group, "description"),
        "permissions": _get(group, "permissions"),
        "data": _get(group, "data"),
        "meta": _get(group, "meta"),
        "parent_group_id": None,
        "member_count": len(exact_ids),
        "created_at": _get(group, "created_at"),
        "updated_at": _get(group, "updated_at"),
    }


def project_group_export(group):
    """Render the current additive export without mutating stored membership."""
    projected = project_group_response(group)
    user_ids = _get(group, "user_ids", [])
    projected["user_ids"] = list(
        dict.fromkeys(value for value in user_ids if isinstance(value, str))
    ) if isinstance(user_ids, list) else []
    return projected


def project_group_info(group):
    """Expose the deliberately small verified-user group summary."""
    projected = project_group_response(group)
    return {
        key: projected[key]
        for key in (
            "id", "user_id", "name", "description", "parent_group_id",
            "member_count", "created_at", "updated_at",
        )
    }


def group_has_resource_access(access_control, group_id):
    """Return whether a legacy ACL directly names the group for read or write."""
    if not isinstance(access_control, Mapping):
        return False
    for permission in ("read", "write"):
        values = access_control.get(permission, {})
        if isinstance(values, Mapping) and group_id in values.get("group_ids", []):
            return True
    return False


def _merge_permissions(base, overlay):
    result = deepcopy(base) if isinstance(base, Mapping) else {}
    if not isinstance(overlay, Mapping):
        return result
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _merge_permissions(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def project_group_preview(group, default_permissions, models=(), tools=(), knowledge=()):
    """Build the measured flat-group preview, including additive current fields."""
    group_id = _get(group, "id")
    direct_permissions = _get(group, "permissions")
    if not isinstance(direct_permissions, Mapping):
        direct_permissions = {}
    inherited = deepcopy(default_permissions) if isinstance(default_permissions, Mapping) else {}
    effective = _merge_permissions(inherited, direct_permissions)

    def resources(rows):
        selected = [
            {"id": _get(row, "id"), "name": _get(row, "name")}
            for row in rows
            if group_has_resource_access(_get(row, "access_control"), group_id)
        ]
        selected.sort(key=lambda row: (str(row["name"]).casefold(), str(row["id"])))
        return {"items": selected, "total": len(selected)}

    return {
        "group": {"id": group_id, "name": _get(group, "name")},
        "permissions": deepcopy(direct_permissions),
        "inherited_permissions": inherited,
        "effective_permissions": effective,
        "default_models": {"local": None, "effective": [], "source_group_id": None},
        "models": resources(models),
        "tools": resources(tools),
        "knowledge": resources(knowledge),
    }


def project_group_members(group, users, membership="direct", query=None, page=1):
    """Build the measured paged DTO for a flat group; never expose user settings."""
    if membership not in ("direct", "inherited", "effective"):
        raise ValueError("membership must be direct, inherited, or effective")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")

    raw_ids = _get(group, "user_ids", [])
    if not isinstance(raw_ids, list):
        raise ValueError("group membership storage is not a JSON array")
    member_ids = list(dict.fromkeys(user_id for user_id in raw_ids if isinstance(user_id, str)))
    users_by_id = {_get(user, "id"): user for user in users if isinstance(_get(user, "id"), str)}
    direct_users = [users_by_id[user_id] for user_id in member_ids if user_id in users_by_id]

    counts = {
        "direct": len(member_ids),
        "effective": len(member_ids),
        "inherited": 0,
    }
    if membership == "inherited":
        selected = []
    else:
        selected = direct_users

    if query:
        needle = query.casefold()
        selected = [
            user
            for user in selected
            if needle in str(_get(user, "name", "")).casefold()
            or needle in str(_get(user, "email", "")).casefold()
        ]

    # The measured unique-name fixture is ordered by casefolded name. Python's
    # stable sort retains membership order for ties; tie/Unicode behavior was
    # not measured and is not claimed as current parity.
    selected.sort(key=lambda user: str(_get(user, "name", "")).casefold())
    total = len(selected)
    start = (page - 1) * MEMBER_PAGE_SIZE
    page_users = selected[start : start + MEMBER_PAGE_SIZE]

    items = [
        {
            "id": _get(user, "id"),
            "name": _get(user, "name"),
            "email": _get(user, "email"),
            "role": _get(user, "role"),
            "membership_type": "direct",
            "is_active": False,
            "last_active_at": None,
            "bio": None,
            "timezone": None,
            "status_emoji": None,
            "status_message": None,
            "status_expires_at": None,
            "groups": [],
            "via_group_ids": [],
        }
        for user in page_users
    ]
    return {"counts": counts, "items": items, "total": total}
