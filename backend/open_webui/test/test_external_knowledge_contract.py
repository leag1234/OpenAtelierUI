# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval

from types import SimpleNamespace

import pytest

from open_webui.models.external_knowledge import (
    ExternalKnowledgeConnectionForm,
    _decrypt_auth_config,
    _encrypt_auth_config,
)
from open_webui.retrieval import external


def test_connection_form_matches_common_required_fields():
    schema = ExternalKnowledgeConnectionForm.model_json_schema()
    assert schema["required"] == ["name", "provider", "endpoint"]


def test_auth_config_is_encrypted_and_round_trips():
    value = {"api_key": "synthetic-secret", "tenant": "round37"}
    encrypted = _encrypt_auth_config(value)
    assert "synthetic-secret" not in encrypted
    assert _decrypt_auth_config(encrypted) == value


@pytest.mark.parametrize(
    "endpoint",
    ("file:///etc/passwd", "ftp://example.invalid/vector", "http://u:p@example.invalid"),
)
def test_endpoint_validation_rejects_non_http_and_embedded_credentials(endpoint):
    with pytest.raises(ValueError):
        external.validate_endpoint(endpoint)


def test_timeout_is_bounded():
    assert external._timeout({"timeout": 0}) == 1.0
    assert external._timeout({"timeout": 300}) == 30.0
    assert external._timeout({"timeout": "invalid"}) == 10.0


def test_qdrant_projection_uses_document_and_metadata_mapping(monkeypatch):
    point = SimpleNamespace(
        score=0.91,
        payload={
            "body": "Synthetic handbook text",
            "heading": "Handbook",
            "origin": "synthetic://round37",
            "link": "https://example.invalid/round37",
            "doc": "doc-1",
            "page_number": 4,
            "extra": {"scope": "synthetic"},
        },
    )

    class Client:
        def __init__(self, **_kwargs):
            pass

        def query_points(self, **kwargs):
            assert kwargs["collection_name"] == "documents"
            assert kwargs["limit"] == 2
            return SimpleNamespace(points=[point])

    monkeypatch.setattr(external, "QdrantClient", Client)
    connection = SimpleNamespace(
        provider="qdrant", endpoint="https://example.invalid", config={"timeout": 2}
    )
    result = external.retrieve(
        connection,
        {"api_key": "synthetic"},
        {
            "name": "documents",
            "config": {
                "content_field": "body",
                "title_field": "heading",
                "source_field": "origin",
                "url_field": "link",
                "document_id_field": "doc",
                "page_field": "page_number",
                "metadata_field": "extra",
            },
        },
        [0.1, 0.2],
        2,
    )
    assert result["documents"] == ["Synthetic handbook text"]
    assert result["distances"] == [0.91]
    assert result["metadatas"] == [
        {
            "scope": "synthetic",
            "external": True,
            "document_id": "doc-1",
            "name": "Handbook",
            "source": "synthetic://round37",
            "url": "https://example.invalid/round37",
            "page": 4,
        }
    ]
