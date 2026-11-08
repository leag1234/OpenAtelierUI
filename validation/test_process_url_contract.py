# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Exercise the measured generic URL-processing compatibility route."""

import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from typing import Optional

from pydantic import BaseModel


ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "backend/open_webui/routers/retrieval.py"


class HTTPError(Exception):
    def __init__(self, *, status_code, detail):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class Loader:
    def __init__(self, documents):
        self.documents = documents

    def load(self):
        if isinstance(self.documents, Exception):
            raise self.documents
        return self.documents


def load_subject(documents, *, bypass=False, save_result=True):
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"), filename=str(ROUTES))
    class_names = {"CollectionNameForm", "ProcessUrlForm", "ProcessUrlResponse"}
    function_names = {"process_url"}
    selected = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in class_names:
            selected.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in function_names:
            node.decorator_list = []
            selected.append(node)
    assert {node.name for node in selected} == class_names | function_names
    effects = []

    def get_web_loader(url, **kwargs):
        effects.append(("load", url, kwargs))
        return Loader(documents)

    def ingestion(requested, user, domain, resource):
        effects.append(("collection", requested, user.id, domain, resource))
        return requested or "generated-collection"

    def save(*args, **kwargs):
        effects.append(("save", args[2], kwargs.get("overwrite"), kwargs["user"].id))
        return save_result

    namespace = {
        "BaseModel": BaseModel,
        "Optional": Optional,
        "Request": object,
        "Query": lambda default, **_kwargs: default,
        "Depends": lambda _value: None,
        "get_verified_user": lambda: None,
        "get_web_loader": get_web_loader,
        "_ingestion_collection": ingestion,
        "save_docs_to_vector_db": save,
        "ProcessUrlResponse": None,
        "HTTPException": HTTPError,
        "status": NS(HTTP_400_BAD_REQUEST=400),
        "ERROR_MESSAGES": NS(DEFAULT=lambda error="": f"[ERROR: {error}]"),
        "log": NS(exception=lambda *_args: None),
    }
    exec(
        compile(ast.Module(body=selected, type_ignores=[]), str(ROUTES), "exec"),
        namespace,
    )
    config = NS(
        ENABLE_WEB_LOADER_SSL_VERIFICATION=True,
        WEB_SEARCH_CONCURRENT_REQUESTS=2,
        BYPASS_WEB_SEARCH_EMBEDDING_AND_RETRIEVAL=bypass,
    )
    request = NS(app=NS(state=NS(config=config)))
    return namespace, request, effects


class ProcessUrlContractTests(unittest.TestCase):
    def setUp(self):
        self.documents = [
            NS(
                page_content="Synthetic workshop page",
                metadata={
                    "title": "Workshop reference",
                    "source": "https://example.invalid",
                },
            )
        ]
        self.user = NS(id="owner", role="user")

    def test_no_process_returns_content_without_vector_write(self):
        subject, request, effects = load_subject(self.documents)
        form = subject["ProcessUrlForm"](url="https://example.invalid/page")
        response = subject["process_url"](request, form, False, user=self.user)
        self.assertEqual(
            response.model_dump(),
            {
                "status": True,
                "type": "web",
                "name": "Workshop reference",
                "url": "https://example.invalid/page",
                "collection_name": None,
                "content": "Synthetic workshop page",
                "file": {
                    "data": {"content": "Synthetic workshop page"},
                    "meta": {
                        "name": "Workshop reference",
                        "source": "https://example.invalid/page",
                    },
                },
            },
        )
        self.assertEqual([effect[0] for effect in effects], ["load"])

    def test_process_saves_to_requested_or_generated_collection(self):
        subject, request, effects = load_subject(self.documents)
        form = subject["ProcessUrlForm"](
            url="https://example.invalid/page",
            collection_name="owned-collection",
        )
        response = subject["process_url"](request, form, True, user=self.user)
        self.assertEqual(response.collection_name, "owned-collection")
        self.assertEqual(
            [effect[0] for effect in effects], ["load", "collection", "save"]
        )
        self.assertEqual(effects[-1], ("save", "owned-collection", True, "owner"))

    def test_bypass_keeps_content_but_reports_no_collection(self):
        subject, request, effects = load_subject(self.documents, bypass=True)
        form = subject["ProcessUrlForm"](url="https://example.invalid/page")
        response = subject["process_url"](request, form, True, user=self.user)
        self.assertIsNone(response.collection_name)
        self.assertEqual([effect[0] for effect in effects], ["load", "collection"])

    def test_empty_or_failed_loader_returns_reference_generic_json_error(self):
        for documents in ([], RuntimeError("sensitive internal detail")):
            subject, request, effects = load_subject(documents)
            form = subject["ProcessUrlForm"](url="http://127.0.0.1/private")
            with self.assertRaises(HTTPError) as caught:
                subject["process_url"](request, form, False, user=self.user)
            self.assertEqual(caught.exception.status_code, 400)
            self.assertEqual(caught.exception.detail, "[ERROR: Error processing URL]")
            self.assertEqual([effect[0] for effect in effects], ["load"])

    def test_declared_schema_matches_the_pinned_public_contract(self):
        source = ROUTES.read_text(encoding="utf-8")
        self.assertIn(
            '@router.post("/process/url", response_model=ProcessUrlResponse)', source
        )
        subject, _request, _effects = load_subject(self.documents)
        response_fields = subject["ProcessUrlResponse"].model_fields
        self.assertEqual(
            list(response_fields),
            ["status", "type", "name", "url", "collection_name", "content", "file"],
        )
        for required in ("status", "type", "name", "url"):
            self.assertTrue(response_fields[required].is_required())
        for optional in ("collection_name", "content", "file"):
            self.assertFalse(response_fields[optional].is_required())


if __name__ == "__main__":
    unittest.main()
