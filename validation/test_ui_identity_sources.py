# SPDX-License-Identifier: BSD-3-Clause
"""Bounded source and asset checks for the supplied UI identity integration."""

import ast
import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT.parent / "ui-inputs" / "openatelierui-logo"


class UiIdentitySourceTests(unittest.TestCase):
    def test_default_display_name_changes_but_custom_name_is_preserved(self):
        source = (ROOT / "backend/open_webui/main.py").read_text()
        tree = ast.parse(source)
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "resolve_public_webui_name"
        )
        namespace = {
            "DEFAULT_WEBUI_NAME": "Open WebUI",
            "PROJECT_WEBUI_NAME": "OpenAtelierUI",
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "main.py", "exec"), namespace)
        resolve_name = namespace["resolve_public_webui_name"]

        self.assertEqual(resolve_name("Open WebUI"), "OpenAtelierUI")
        self.assertEqual(resolve_name("Open WebUI", custom_name="existing"), "Open WebUI")
        self.assertEqual(resolve_name("Community workshop"), "Community workshop")
        self.assertIn('title=resolve_public_webui_name(WEBUI_NAME, os.environ.get("CUSTOM_NAME", ""))', source)
        self.assertIn(
            'app.state.WEBUI_NAME = resolve_public_webui_name(WEBUI_NAME, os.environ.get("CUSTOM_NAME", ""))',
            source,
        )

    def test_runtime_static_paths_exist_in_the_backend_static_directory(self):
        source_paths = [
            "favicon.png",
            "favicon-dark.png",
            "favicon-96x96.png",
            "favicon.ico",
            "splash.png",
            "splash-dark.png",
            "apple-touch-icon.png",
            "logo.png",
            "openatelierui-logotype.svg",
            "openatelierui-logotype-dark.svg",
            "openatelierui-symbol-dark.svg",
        ]
        for relative in source_paths:
            with self.subTest(path=relative):
                self.assertTrue((ROOT / "backend/open_webui/static" / relative).is_file())

    def test_supplied_svg_and_png_copies_preserve_the_user_asset_bytes(self):
        copies = {
            "svg/openatelierui-logotype.svg": [
                "static/static/openatelierui-logotype.svg",
                "backend/open_webui/static/openatelierui-logotype.svg",
            ],
            "svg/openatelierui-logotype-dark.svg": [
                "static/static/openatelierui-logotype-dark.svg",
                "backend/open_webui/static/openatelierui-logotype-dark.svg",
            ],
            "svg/openatelierui-symbol.svg": [
                "static/static/favicon.svg",
                "backend/open_webui/static/favicon.svg",
            ],
            "svg/openatelierui-symbol-dark.svg": [
                "static/static/openatelierui-symbol-dark.svg",
                "backend/open_webui/static/openatelierui-symbol-dark.svg",
            ],
            "png/openatelierui-symbol-64.png": [
                "static/static/favicon.png",
                "backend/open_webui/static/favicon.png",
            ],
            "png/openatelierui-symbol-128.png": [
                "static/static/favicon-96x96.png",
                "backend/open_webui/static/favicon-96x96.png",
            ],
            "png/openatelierui-symbol-256.png": [
                "static/static/apple-touch-icon.png",
                "backend/open_webui/static/apple-touch-icon.png",
            ],
            "png/openatelierui-symbol-512.png": [
                "static/static/splash.png",
                "backend/open_webui/static/splash.png",
                "backend/open_webui/static/logo.png",
            ],
            "png/openatelierui-symbol-dark-512.png": [
                "static/static/favicon-dark.png",
                "backend/open_webui/static/favicon-dark.png",
                "static/static/splash-dark.png",
                "backend/open_webui/static/splash-dark.png",
            ],
        }
        for source, targets in copies.items():
            expected = (ASSETS / source).read_bytes()
            expected_hash = hashlib.sha256(expected).hexdigest()
            for target in targets:
                with self.subTest(source=source, target=target):
                    actual = (ROOT / target).read_bytes()
                    self.assertEqual(hashlib.sha256(actual).hexdigest(), expected_hash)

    def test_legacy_ico_contains_the_supplied_32px_symbol_png(self):
        expected = (ASSETS / "png/openatelierui-symbol-32.png").read_bytes()
        for relative in (
            "static/static/favicon.ico",
            "backend/open_webui/static/favicon.ico",
        ):
            with self.subTest(path=relative):
                ico = (ROOT / relative).read_bytes()
                self.assertGreaterEqual(len(ico), 22)
                self.assertEqual(ico[:4], b"\x00\x00\x01\x00")
                image_count = int.from_bytes(ico[4:6], "little")
                self.assertEqual(image_count, 1)
                image_size = int.from_bytes(ico[14:18], "little")
                image_offset = int.from_bytes(ico[18:22], "little")
                self.assertEqual(ico[image_offset : image_offset + image_size], expected)

    def test_visible_sources_use_served_legacy_paths_and_supplied_wordmark(self):
        app_html = (ROOT / "src/app.html").read_text()
        app_layout = (ROOT / "src/routes/+layout.svelte").read_text()
        sign_in = (ROOT / "src/routes/auth/+page.svelte").read_text()
        self.assertIn('href="/static/favicon.png"', app_html)
        self.assertIn("/static/splash.png", app_html)
        self.assertIn("/static/favicon.png", app_layout)
        self.assertIn("/static/openatelierui-logotype.svg", sign_in)
        self.assertIn("/static/openatelierui-logotype-dark.svg", sign_in)
        self.assertNotIn("openatelierui-symbol-", app_html)
        sidebar = (ROOT / "src/lib/components/app/AppSidebar.svelte").read_text()
        self.assertIn('aria-label="Home"', sidebar)
        self.assertIn("{#if $WEBUI_NAME === 'OpenAtelierUI'}", sidebar)
        self.assertIn('src="/static/splash.png"', sidebar)
        self.assertIn("{#if $WEBUI_NAME === 'OpenAtelierUI'}", sign_in)
        self.assertIn('src="{WEBUI_BASE_URL}/static/splash.png"', sign_in)
        self.assertIn('class="h-7 w-44 object-contain object-left"', sign_in)
        self.assertIn('alt=""\n\t\t\t\t\t\taria-hidden="true"', sidebar)

    def test_backend_manifest_uses_the_served_symbol_and_name(self):
        source = (ROOT / "backend/open_webui/main.py").read_text()
        self.assertIn('"src": "/static/logo.png"', source)
        self.assertIn('"sizes": "512x512"', source)
        self.assertIn("/static/favicon.png</Image>", source)
        logo = ROOT / "backend/open_webui/static/logo.png"
        supplied = ASSETS / "png/openatelierui-symbol-512.png"
        self.assertEqual(logo.read_bytes(), supplied.read_bytes())

    def test_pinned_config_source_remains_unchanged(self):
        digest = hashlib.sha256((ROOT / "backend/open_webui/config.py").read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            "e86c2da3487ddf9d9ee9fe5bafc81f5e96d8bc8a800a5632e3387bec6e57d694",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
