# SPDX-License-Identifier: BSD-3-Clause
"""Narrow current-style projections for the measured flat-group REST bridge."""

from collections.abc import Mapping


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
