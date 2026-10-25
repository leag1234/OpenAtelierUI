# SPDX-License-Identifier: BSD-3-Clause
"""Translate the measured current read-grant subset into the legacy ACL."""

from collections.abc import Mapping


_GRANT_FIELDS = {"principal_type", "principal_id", "permission"}
_ROUNDTRIP_FIELDS = {"id", "resource_type", "resource_id", "created_at"}


def _read_acl(grants):
    """Return a legacy ACL for the supported current read grants.

    Empty grants are private, user IDs are targeted, and the user wildcard is
    the old runtime's public-read representation. Group and write semantics
    are deliberately unsupported.
    """
    if grants is None or grants == []:
        return {}
    if not isinstance(grants, list):
        raise ValueError("access_grants must be null or a list of read grants")

    user_ids = []
    seen = set()
    for grant in grants:
        if not isinstance(grant, Mapping):
            raise ValueError("each access grant must be an object")
        keys = set(grant)
        if keys - (_GRANT_FIELDS | _ROUNDTRIP_FIELDS) or not _GRANT_FIELDS <= keys:
            raise ValueError("access grant has missing or unsupported fields")
        if grant["principal_type"] != "user":
            raise ValueError("only user principals are supported; group grants are not implemented")
        principal_id = grant["principal_id"]
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError("access grant principal_id must be a nonempty string")
        if grant["permission"] != "read":
            raise ValueError("only read permission is supported; write grants are not implemented")
        if principal_id in seen:
            raise ValueError("duplicate user read grants are not supported")
        seen.add(principal_id)
        user_ids.append(principal_id)

    if "*" in seen:
        if len(seen) != 1:
            raise ValueError("public read cannot be combined with user-specific grants")
        return None
    if not user_ids:
        return {}
    return {"read": {"user_ids": user_ids}}


def access_control_for_grants(grants):
    """Translate grants for dedicated ACL mutation endpoints (null is invalid)."""
    if not isinstance(grants, list):
        raise ValueError("access_grants must be a list")
    return _read_acl(grants)


def convert_explicit_access_grants(data):
    """Map current form input to the existing ACL without mutating input.

    Current clients use ``access_grants``. An omitted field and obsolete
    ``access_control`` extras are private on create. On ordinary edits routers
    restore the stored ACL after validation, so omitted/null/empty grants do
    not revoke sharing. Server grant IDs/timestamps are accepted for safe
    round-trip input but are not persisted as authority.
    """
    if not isinstance(data, Mapping):
        return data
    converted = dict(data)
    grants = converted.pop("access_grants", None)
    # Ignore old ACL-shaped request extras: they are not part of the current
    # contract and must never turn a private current-style create public.
    converted.pop("access_control", None)
    converted["access_control"] = _read_acl(grants)
    return converted


def access_grants_for_acl(access_control, resource_type, resource_id, created_at=0):
    """Render supported legacy ACLs as current-style grant metadata."""
    if access_control is None:
        grants = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]
    else:
        read = access_control.get("read", {}) if isinstance(access_control, dict) else {}
        if not isinstance(read, dict) or read.get("group_ids"):
            return []
        user_ids = read.get("user_ids", [])
        if not isinstance(user_ids, list) or any(not isinstance(value, str) for value in user_ids):
            return []
        grants = [
            {"principal_type": "user", "principal_id": user_id, "permission": "read"}
            for user_id in user_ids
        ]
    for grant in grants:
        grant.update(
            {
                "id": f"{resource_type}:{resource_id}:read:{grant['principal_id']}",
                "resource_type": resource_type,
                "resource_id": resource_id,
                "created_at": int(created_at),
            }
        )
    return grants


def preserve_access_control_for_update(payload, existing_access_control):
    """Keep sharing unchanged when ordinary edit forms carry default ACLs."""
    updated = dict(payload)
    updated["access_control"] = existing_access_control
    return updated
