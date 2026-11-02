#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Focused regression checks for the reported unchanged Atlas integration contracts."""

import ast
import asyncio
import base64
import binascii
from pathlib import Path
import tempfile
import types
import unittest
from urllib.parse import unquote

from fastapi import HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from starlette.responses import Response


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "backend/open_webui/main.py"
MODELS = ROOT / "backend/open_webui/routers/models.py"
USERS = ROOT / "backend/open_webui/routers/users.py"
NOTES = ROOT / "backend/open_webui/routers/notes.py"
OLLAMA = ROOT / "backend/open_webui/routers/ollama.py"
SRC = ROOT / "src"
CHAT_NAVBAR = SRC / "lib/components/chat/Navbar.svelte"
LAYOUT_NAVBAR = SRC / "lib/components/layout/Navbar.svelte"
MESSAGE_INPUT = SRC / "lib/components/chat/MessageInput.svelte"
TOOL_SERVERS_MODAL = SRC / "lib/components/chat/ToolServersModal.svelte"
SIDEBAR = SRC / "lib/components/layout/Sidebar.svelte"


def extracted_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            node.decorator_list = []
            selected.append(node)
    if {node.name for node in selected} != set(names):
        raise AssertionError("expected source functions were not found")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


class Store:
    model = None

    @classmethod
    def get_model_by_id(cls, _model_id):
        return cls.model


class AtlasDropInReportTests(unittest.TestCase):
    def test_container_keeps_the_legacy_atlas_bootstrap_entrypoint(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        wrapper = ROOT / "packaging" / "start-with-atlas.sh"
        self.assertTrue(wrapper.is_file())
        self.assertIn(
            "./packaging/start-with-atlas.sh /opt/openatelier/start-with-atlas.sh",
            dockerfile,
        )
        source = wrapper.read_text()
        self.assertIn("open_webui.utils.async_model_bootstrap", source)
        self.assertIn("exec bash /app/backend/start.sh", source)

    def test_image_build_pins_compatible_embeddings_and_fails_closed_on_prefetch(self):
        requirements = (ROOT / "backend/requirements.txt").read_text(encoding="utf-8")
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        for dependency in (
            "numpy==1.26.4",
            "pyarrow==19.0.0",
            "sentence-transformers==3.3.1",
            "chardet==5.2.0",
        ):
            self.assertEqual(requirements.count(dependency), 1)
            self.assertEqual(project.count(dependency), 1)
        self.assertEqual(dockerfile.count("assert numpy.__version__ == '1.26.4'"), 2)
        self.assertEqual(dockerfile.count("assert pyarrow.__version__ == '19.0.0'"), 2)
        self.assertNotIn("download_root=os.environ['WHISPER_MODEL_DIR'])\";", dockerfile)
        self.assertIn(
            'SENTENCE_TRANSFORMERS_HOME="/opt/openatelierui/cache/embedding/models"',
            dockerfile,
        )
        self.assertNotIn(
            'SENTENCE_TRANSFORMERS_HOME="/app/backend/data/cache/embedding/models"',
            dockerfile,
        )

    def test_default_feature_activation_matches_existing_integration_inputs(self):
        ns = extracted_functions(
            MAIN,
            {"notes_compat_enabled", "terminal_compat_enabled", "spa_fallback_allowed"},
            {},
        )
        self.assertTrue(ns["notes_compat_enabled"]({}))
        self.assertFalse(ns["notes_compat_enabled"]({"OPENATELIER_PRIVATE_NOTES_COMPAT": "0"}))
        self.assertTrue(ns["terminal_compat_enabled"]({"TERMINAL_SERVER_CONNECTIONS": "[]"}))
        self.assertFalse(ns["terminal_compat_enabled"]({}))
        self.assertFalse(ns["spa_fallback_allowed"]("api/v1/unknown"))
        self.assertTrue(ns["spa_fallback_allowed"]("c/synthetic-chat"))
        self.assertFalse(ns["spa_fallback_allowed"]("_app/example.js"))
        main_source = MAIN.read_text(encoding="utf-8")
        self.assertIn("if NOTES_COMPAT_ENABLED:", main_source)
        self.assertIn("if TERMINAL_COMPAT_ENABLED:", main_source)
        self.assertIn("if not spa_fallback_allowed(path):", main_source)
        self.assertIn('.get("OPENATELIER_PRIVATE_NOTES_COMPAT", "1") != "0"', NOTES.read_text(encoding="utf-8"))

    def model_routes(self, static_dir):
        status = types.SimpleNamespace(HTTP_401_UNAUTHORIZED=401, HTTP_404_NOT_FOUND=404)
        errors = types.SimpleNamespace(
            NOT_FOUND="not found", ACCESS_PROHIBITED="access prohibited"
        )
        return extracted_functions(
            MODELS,
            {"get_model_by_id", "get_model_profile_image"},
            {
                "Models": Store,
                "Depends": lambda dependency: dependency,
                "get_verified_user": lambda: None,
                "HTTPException": HTTPException,
                "status": status,
                "ERROR_MESSAGES": errors,
                "has_access": lambda *_args: False,
                "Path": Path,
                "unquote": unquote,
                "STATIC_DIR": static_dir,
                "FileResponse": FileResponse,
                "RedirectResponse": RedirectResponse,
                "Response": Response,
                "base64": base64,
                "binascii": binascii,
            },
        )

    def test_missing_model_is_404_but_existing_private_model_is_401(self):
        routes = self.model_routes("/tmp")
        user = types.SimpleNamespace(id="caller", role="admin")
        Store.model = None
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(routes["get_model_by_id"]("missing", user))
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(caught.exception.detail, "not found")

        Store.model = types.SimpleNamespace(
            id="private", user_id="owner", access_control={},
            meta=types.SimpleNamespace(profile_image_url="/static/favicon.png"),
        )
        user = types.SimpleNamespace(id="caller", role="user")
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(routes["get_model_profile_image"]("private", user))
        self.assertEqual(caught.exception.status_code, 401)

    def test_model_profile_image_serves_bounded_local_static_file(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "atlas" / "avatar.png"
            image.parent.mkdir()
            image.write_bytes(b"synthetic-png")
            routes = self.model_routes(directory)
            Store.model = types.SimpleNamespace(
                id="atlas", user_id="owner", access_control=None,
                meta=types.SimpleNamespace(profile_image_url="/static/atlas/avatar.png"),
            )
            user = types.SimpleNamespace(id="owner", role="user")
            response = asyncio.run(routes["get_model_profile_image"]("atlas", user))
            self.assertIsInstance(response, FileResponse)
            self.assertEqual(Path(response.path), image.resolve())

    def test_unknown_model_profile_image_serves_local_default(self):
        with tempfile.TemporaryDirectory() as directory:
            default_image = Path(directory) / "favicon.png"
            default_image.write_bytes(b"synthetic-default-png")
            routes = self.model_routes(directory)
            Store.model = None
            response = asyncio.run(routes["get_model_profile_image"](
                "not-created-yet", types.SimpleNamespace(id="member", role="user")
            ))
            self.assertIsInstance(response, FileResponse)
            self.assertEqual(Path(response.path), default_image.resolve())

    def test_admin_user_list_alias_matches_reference_envelope_and_precedes_dynamic_route(self):
        tree = ast.parse(USERS.read_text(encoding="utf-8"))
        get_users = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_users")
        get_users_all = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_users_all")

        def paths(route):
            values = []
            for decorator in route.decorator_list:
                if isinstance(decorator, ast.Call) and decorator.args:
                    value = decorator.args[0]
                    if isinstance(value, ast.Constant):
                        values.append(value.value)
            return values

        self.assertEqual(paths(get_users), ["/"])
        self.assertEqual(paths(get_users_all), ["/all"])
        self.assertIn("user=Depends(get_admin_user)", ast.unparse(get_users_all))
        source = USERS.read_text(encoding="utf-8")
        self.assertIn('response_model=UserInfoListResponse', source)
        self.assertIn('"users": [_user_info_projection(item) for item in users]', source)
        self.assertLess(source.index('async def get_users_all'), source.index('async def get_user_by_id'))

    def test_admin_user_list_projection_is_secret_free(self):
        namespace = extracted_functions(
            USERS,
            {"_user_info_projection"},
            {"Groups": types.SimpleNamespace(get_groups_by_member_id=lambda _id: [])},
        )
        user = types.SimpleNamespace(
            id="member", name="Member", email="member@example.invalid", role="user",
            bio=None, info={"status_message": "Available"}, api_key="must-not-leak",
        )
        projected = namespace["_user_info_projection"](user)
        self.assertEqual(projected["status_message"], "Available")
        self.assertNotIn("api_key", projected)
        self.assertNotIn("profile_image_url", projected)

    def test_all_reported_dom_hooks_exist_once(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in SRC.rglob("*.svelte")
        )
        for element_id in (
            "input-menu-button",
            "message-input-container",
            "stop-response-button",
            "main-content",
            "sidebar-webui-name",
            "pinned-menu-items-list",
            "tab-general",
        ):
            self.assertEqual(source.count(f'id="{element_id}"'), 1, element_id)

    def test_reference_like_composer_keeps_model_and_integrations_controls_together(self):
        navbar = CHAT_NAVBAR.read_text(encoding="utf-8")
        layout_navbar = LAYOUT_NAVBAR.read_text(encoding="utf-8")
        composer = MESSAGE_INPUT.read_text(encoding="utf-8")
        integrations = TOOL_SERVERS_MODAL.read_text(encoding="utf-8")
        self.assertIn("export let showModelSelector = false;", navbar)
        self.assertIn(
            'class="md:hidden mr-1 self-start flex flex-none items-center', navbar
        )
        self.assertIn(
            'class="md:hidden mr-1 self-start flex flex-none items-center',
            layout_navbar,
        )
        self.assertEqual(
            composer.count("<ModelSelector bind:selectedModels showSetDefault={false} />"),
            1,
        )
        self.assertIn("aria-label={$i18n.t('Integrations')}", composer)
        self.assertIn("flex shrink-0 gap-1 items-center whitespace-nowrap", composer)
        self.assertIn('<span class="sr-only">{$i18n.t(\'Integrations\')}</span>', composer)
        self.assertIn("bind:codeInterpreterEnabled", composer)
        self.assertIn("bind:webSearchEnabled", composer)
        self.assertIn("bind:imageGenerationEnabled", composer)
        self.assertIn("aria-pressed={codeInterpreterEnabled}", integrations)
        self.assertIn("aria-pressed={webSearchEnabled}", integrations)
        self.assertIn("aria-pressed={imageGenerationEnabled}", integrations)

    def test_collapsed_desktop_navigation_keeps_primary_journeys_discoverable(self):
        sidebar = SIDEBAR.read_text(encoding="utf-8")
        self.assertIn('id="collapsed-navigation-rail"', sidebar)
        self.assertIn("{#if !$showSidebar && !$mobile}", sidebar)
        for label in ("Open Sidebar", "New Chat", "Search", "Notes", "Workspace", "User menu"):
            self.assertIn(f"$i18n.t('{label}')", sidebar)

    def test_composer_file_chip_exposes_name_then_size_siblings(self):
        source = (SRC / "lib/components/common/FileItem.svelte").read_text(encoding="utf-8")
        name = 'class="dark:text-gray-100 text-sm font-medium truncate flex-1"'
        size = 'class="text-gray-500 text-xs capitalize shrink-0"'
        self.assertEqual(source.count(name), 1)
        self.assertGreater(source.index(size), source.index(name))
        between = source[source.index(name):source.index(size)]
        self.assertNotIn("</div>\n\t\t\t</div>", between)
        self.assertIn("{dismissible ? 'pr-5' : ''}", source)
        self.assertIn("aria-label={$i18n.t('Remove file')}", source)
        self.assertIn("focus-visible:outline-blue-600", source)
        self.assertNotIn("group-hover:visible invisible", source)
        outer_end = source.rindex("</button>")
        remove_start = source.index("aria-label={$i18n.t('Remove file')}")
        self.assertLess(remove_start, outer_end)
        self.assertIn('<div class="absolute -top-1 -right-1 z-10">', source)

    def test_active_chat_owns_unique_response_controls_and_title_hook(self):
        composer = MESSAGE_INPUT.read_text(encoding="utf-8")
        placeholder = (SRC / "lib/components/chat/Placeholder.svelte").read_text(encoding="utf-8")
        response = (SRC / "lib/components/chat/Messages/ResponseMessage.svelte").read_text(encoding="utf-8")
        multi = (SRC / "lib/components/chat/Messages/MultiResponseMessages.svelte").read_text(encoding="utf-8")
        navbar = CHAT_NAVBAR.read_text(encoding="utf-8")
        self.assertIn("export let exposeResponseControls = true;", composer)
        self.assertIn("exposeResponseControls={false}", placeholder)
        self.assertEqual(response.count("regenerate-response-button"), 1)
        self.assertEqual(response.count("transition continue-response-button"), 1)
        self.assertEqual(response.count("transition delete-response-button"), 1)
        regenerate_block = response[
            response.index("<Tooltip content={$i18n.t('Regenerate')}"):
            response.index("<Tooltip content={$i18n.t('Delete')}")
        ]
        delete_block = response[response.index("<Tooltip content={$i18n.t('Delete')}"):]
        self.assertIn("transition regenerate-response-button", regenerate_block)
        self.assertNotIn("delete-response-button", regenerate_block)
        self.assertIn("transition delete-response-button", delete_block)
        self.assertNotIn("regenerate-response-button", delete_block)
        self.assertNotIn("regenerate-response-button", multi)
        self.assertIn('class="truncate text-left" title={title}', navbar)

    def test_ollama_version_route_requires_verified_identity(self):
        tree = ast.parse(OLLAMA.read_text(encoding="utf-8"))
        route = next(
            node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_ollama_versions"
        )
        user_index = next(
            index for index, argument in enumerate(route.args.args)
            if argument.arg == "user"
        )
        default_index = user_index - (len(route.args.args) - len(route.args.defaults))
        default = route.args.defaults[default_index]
        self.assertIsInstance(default, ast.Call)
        self.assertEqual(default.func.id, "Depends")
        self.assertEqual(default.args[0].id, "get_verified_user")

    def test_model_delete_keeps_current_method_and_reference_post_alias(self):
        tree = ast.parse(MODELS.read_text(encoding="utf-8"))
        routes = {
            node.name: node for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name in {"delete_model_by_id", "delete_model_by_id_post"}
        }
        self.assertEqual(set(routes), {"delete_model_by_id", "delete_model_by_id_post"})
        self.assertEqual(routes["delete_model_by_id"].decorator_list[0].func.attr, "delete")
        self.assertEqual(routes["delete_model_by_id_post"].decorator_list[0].func.attr, "post")
        self.assertIn("form_data: ModelIdForm", ast.unparse(routes["delete_model_by_id_post"]))
        self.assertIn("_delete_model_by_id(form_data.id, user)", ast.unparse(routes["delete_model_by_id_post"]))


if __name__ == "__main__":
    unittest.main()
