# SPDX-License-Identifier: BSD-3-Clause
"""Small dependency-free model API compatibility helpers."""
from typing import Optional


def resolve_model_update_id(query_id: Optional[str], body_id: str) -> str:
    """Resolve the v0.11 body id while rejecting ambiguous update targets."""
    if query_id is not None and query_id != body_id:
        raise ValueError("model update ids do not match")
    return query_id or body_id
