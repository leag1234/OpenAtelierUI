# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Small deterministic helpers for the measured local model-read contract."""

from __future__ import annotations

import uuid


_MODEL_VERSION_NAMESPACE = uuid.UUID("ad3f3bf7-0e61-4daa-a148-a8d883e943d8")


def model_tag_names(model) -> list[str]:
    """Return unique, sorted tag names without trusting arbitrary metadata."""
    meta = getattr(model, "meta", None)
    if hasattr(meta, "model_dump"):
        meta = meta.model_dump()
    tags = meta.get("tags", []) if isinstance(meta, dict) else []
    names = set()
    if isinstance(tags, list):
        for tag in tags:
            name = tag.get("name") if isinstance(tag, dict) else tag
            if isinstance(name, str) and name:
                names.add(name)
    return sorted(names)


def filter_models(models, *, query=None, tag=None, order_by=None, direction=None):
    """Apply the observed name/tag/order subset to an already authorized set."""
    values = list(models)
    if query:
        needle = query.casefold()
        values = [model for model in values if needle in model.name.casefold()]
    if tag:
        values = [model for model in values if tag in model_tag_names(model)]
    if order_by in {"name", "created_at", "updated_at"}:
        values.sort(
            key=lambda model: (getattr(model, order_by), model.id),
            reverse=direction == "desc",
        )
    return values


def paginate(values, page, size=30):
    normalized = max(page or 1, 1)
    offset = (normalized - 1) * size
    return values[offset : offset + size]


def local_model_version_id(model) -> str:
    """Return a stable opaque version identifier that changes on model update."""
    material = f"{model.id}\0{model.user_id}\0{model.updated_at}"
    return str(uuid.uuid5(_MODEL_VERSION_NAMESPACE, material))
