# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Bounded retrieval from administrator-configured external vector stores."""

from typing import Any
from urllib.parse import urlsplit

import requests
from qdrant_client import QdrantClient


DEFAULT_FIELDS = {
    "content_field": "content",
    "title_field": "title",
    "source_field": "source",
    "url_field": "url",
    "document_id_field": "document_id",
    "page_field": "page",
    "metadata_field": "metadata",
    "score_field": "score",
}


def validate_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("External knowledge endpoint must be an HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("External knowledge credentials must use auth_config")
    return endpoint.rstrip("/")


def _timeout(config: dict[str, Any] | None) -> float:
    raw = (config or {}).get("timeout", 10)
    try:
        return max(1.0, min(float(raw), 30.0))
    except (TypeError, ValueError):
        return 10.0


def test_connection(connection, auth_config: dict[str, Any] | None) -> None:
    if connection.provider != "qdrant":
        raise ValueError(f"Unsupported external knowledge provider: {connection.provider}")
    headers = {}
    api_key = (auth_config or {}).get("api_key")
    if api_key:
        headers["api-key"] = api_key
    response = requests.get(
        validate_endpoint(connection.endpoint),
        headers=headers,
        timeout=_timeout(connection.config),
    )
    response.raise_for_status()


def retrieve(
    connection,
    auth_config: dict[str, Any] | None,
    source: dict[str, Any],
    vector: list[float],
    count: int,
) -> dict[str, list]:
    if connection.provider != "qdrant":
        raise ValueError(f"Unsupported external knowledge provider: {connection.provider}")
    config = source.get("config") if isinstance(source.get("config"), dict) else {}
    fields = {key: config.get(key, default) for key, default in DEFAULT_FIELDS.items()}
    collection = config.get("collection") or source.get("name")
    client = QdrantClient(
        url=validate_endpoint(connection.endpoint),
        api_key=(auth_config or {}).get("api_key"),
        timeout=_timeout(connection.config),
    )
    points = client.query_points(
        collection_name=collection,
        query=vector,
        limit=max(1, min(int(count), 100)),
        with_payload=True,
        with_vectors=False,
    ).points
    documents, metadatas, distances = [], [], []
    for point in points:
        payload = point.payload if isinstance(point.payload, dict) else {}
        content = payload.get(fields["content_field"], "")
        metadata = payload.get(fields["metadata_field"], {})
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        metadata.update(
            {
                "external": True,
                "document_id": payload.get(fields["document_id_field"]),
                "name": payload.get(fields["title_field"]),
                "source": payload.get(fields["source_field"]),
                "url": payload.get(fields["url_field"]),
                "page": payload.get(fields["page_field"]),
            }
        )
        documents.append(content if isinstance(content, str) else str(content))
        metadatas.append(metadata)
        distances.append(float(getattr(point, "score", 0.0)))
    return {"distances": distances, "documents": documents, "metadatas": metadatas}
