# SPDX-License-Identifier: BSD-3-Clause
"""Fail-closed handling for explicit grants unsupported by the old ACL model."""

from collections.abc import Mapping


def convert_explicit_access_grants(data):
    """Handle explicit empty grants, refusing rights the old runtime cannot match.

    Omitted ``access_grants`` deliberately leaves legacy ``access_control``
    handling untouched. Explicit nonempty grants are rejected because old tool
    responses cannot preserve the measured distinction between specs and source.
    """
    if not isinstance(data, Mapping) or "access_grants" not in data:
        return data

    if "access_control" in data:
        raise ValueError("access_grants cannot be combined with access_control")

    grants = data["access_grants"]
    converted = dict(data)
    del converted["access_grants"]

    if grants is None or grants == []:
        converted["access_control"] = {}
        return converted

    if not isinstance(grants, list):
        raise ValueError("access_grants must be null or a list of user read grants")
    if grants:
        raise ValueError(
            "nonempty access_grants are unsupported: the old runtime cannot preserve "
            "per-principal source and rights semantics"
        )
    converted["access_control"] = {}
    return converted
