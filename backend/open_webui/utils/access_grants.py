# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Translate the measured current read-grant subset into the legacy ACL."""

from collections.abc import Mapping


_GRANT_FIELDS = {"principal_type", "principal_id", "permission"}
_ROUNDTRIP_FIELDS = {"id", "resource_type", "resource_id", "created_at"}


def _read_acl(grants):
    """Return a legacy ACL for the supported current read grants.

    Empty grants are private, user and group IDs are targeted, and the user
    wildcard is the old runtime's public-read representation. Write grants
    and wildcard groups are deliberately unsupported.
    """
    if grants is None or grants == []:
        return {}
    if not isinstance(grants, list):
        raise ValueError("access_grants must be null or a list of read grants")

    user_ids = []
    group_ids = []
    seen = set()
    for grant in grants:
        if not isinstance(grant, Mapping):
            raise ValueError("each access grant must be an object")
        keys = set(grant)
        if keys - (_GRANT_FIELDS | _ROUNDTRIP_FIELDS) or not _GRANT_FIELDS <= keys:
            raise ValueError("access grant has missing or unsupported fields")
        principal_type = grant["principal_type"]
        if principal_type not in ("user", "group"):
            raise ValueError("only user and group principals are supported")
        principal_id = grant["principal_id"]
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError("access grant principal_id must be a nonempty string")
        if principal_type == "group" and principal_id == "*":
            raise ValueError("wildcard group grants are not supported")
        if grant["permission"] != "read":
            raise ValueError(
                "only read permission is supported; write grants are not implemented"
            )
        identity = (principal_type, principal_id)
        if identity in seen:
            raise ValueError("duplicate read grants are not supported")
        seen.add(identity)
        if principal_type == "user":
            user_ids.append(principal_id)
        else:
            group_ids.append(principal_id)

    if "*" in user_ids:
        if len(seen) != 1:
            raise ValueError("public read cannot be combined with user-specific grants")
        return None
    if not user_ids and not group_ids:
        return {}
    read = {}
    if user_ids:
        read["user_ids"] = user_ids
    if group_ids:
        read["group_ids"] = group_ids
    return {"read": read}


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
        read = (
            access_control.get("read", {}) if isinstance(access_control, dict) else {}
        )
        if not isinstance(read, dict):
            return []
        user_ids = read.get("user_ids", [])
        group_ids = read.get("group_ids", [])
        if (
            not isinstance(user_ids, list)
            or any(
                not isinstance(value, str) or not value.strip() for value in user_ids
            )
            or not isinstance(group_ids, list)
            or any(
                not isinstance(value, str) or not value.strip() for value in group_ids
            )
        ):
            return []
        grants = [
            {"principal_type": "user", "principal_id": user_id, "permission": "read"}
            for user_id in user_ids
        ]
        grants.extend(
            {"principal_type": "group", "principal_id": group_id, "permission": "read"}
            for group_id in group_ids
        )
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


def knowledge_access_control_for_grants(grants):
    """Translate the measured knowledge read/write grant subset into an ACL."""
    if grants is None or grants == []:
        return {}
    if not isinstance(grants, list):
        raise ValueError("access_grants must be a list")
    acl = {}
    seen = set()
    for grant in grants:
        if not isinstance(grant, Mapping):
            raise ValueError("each access grant must be an object")
        principal_type = grant.get("principal_type")
        principal_id = grant.get("principal_id")
        permission = grant.get("permission")
        if principal_type not in ("user", "group"):
            raise ValueError("only user and group principals are supported")
        if not isinstance(principal_id, str) or not principal_id.strip():
            raise ValueError("access grant principal_id must be a nonempty string")
        if permission not in ("read", "write"):
            raise ValueError("only read and write permissions are supported")
        if principal_id == "*" and (principal_type != "user" or permission != "read"):
            raise ValueError("only the public user read grant may use a wildcard")
        identity = (principal_type, principal_id, permission)
        if identity in seen:
            raise ValueError("duplicate access grants are not supported")
        seen.add(identity)
        target = acl.setdefault(permission, {}).setdefault(
            "user_ids" if principal_type == "user" else "group_ids", []
        )
        target.append(principal_id)
    if acl == {"read": {"user_ids": ["*"]}}:
        return None
    return acl


def knowledge_access_grants_for_acl(
    access_control, resource_type, resource_id, created_at=0
):
    """Render read and write knowledge ACLs as current-style grant metadata."""
    if access_control is None:
        grants = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]
    elif not isinstance(access_control, Mapping):
        return []
    else:
        grants = []
        for permission in ("read", "write"):
            values = access_control.get(permission, {})
            if not isinstance(values, Mapping):
                continue
            for principal_type, key in (("user", "user_ids"), ("group", "group_ids")):
                identifiers = values.get(key, [])
                if not isinstance(identifiers, list):
                    continue
                for principal_id in identifiers:
                    if isinstance(principal_id, str) and principal_id:
                        grants.append(
                            {
                                "principal_type": principal_type,
                                "principal_id": principal_id,
                                "permission": permission,
                            }
                        )
    for grant in grants:
        grant.update(
            {
                "id": f"{resource_type}:{resource_id}:{grant['permission']}:{grant['principal_id']}",
                "resource_type": resource_type,
                "resource_id": resource_id,
                "created_at": int(created_at),
            }
        )
    return grants
