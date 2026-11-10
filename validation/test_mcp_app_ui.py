# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval
"""Static security checks for the MCP app resource renderer."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRAME = ROOT / "src/lib/components/common/MCPAppFrame.svelte"
COLLAPSIBLE = ROOT / "src/lib/components/common/Collapsible.svelte"
API = ROOT / "src/lib/apis/tools/index.ts"


class MCPAppUITests(unittest.TestCase):
    def test_rpc_path_encodes_server_identifier_and_uses_bearer(self):
        source = API.read_text(encoding="utf-8")
        self.assertIn("encodeURIComponent(serverId)", source)
        self.assertIn("authorization: `Bearer ${token}`", source)
        self.assertIn("/mcp/app/rpc", source)

    def test_rpc_forwards_an_optional_abort_signal(self):
        source = API.read_text(encoding="utf-8")
        self.assertIn("signal?: AbortSignal", source)
        self.assertIn("signal\n", source)

    def test_renderer_is_unique_origin_and_blocks_network_by_default(self):
        source = FRAME.read_text(encoding="utf-8")
        self.assertIn('sandbox="allow-scripts"', source)
        self.assertNotIn("allow-same-origin", source)
        self.assertNotIn("allow-forms", source)
        self.assertIn("default-src 'none'", source)
        self.assertIn("form-action 'none'", source)
        self.assertIn("frame-ancestors 'none'", source)

    def test_only_signed_tool_result_metadata_mounts_app_renderer(self):
        source = COLLAPSIBLE.read_text(encoding="utf-8")
        self.assertIn("parsedResult?._meta?.openatelier_mcp_app", source)
        self.assertIn("<MCPAppFrame app={parsedResult._meta.openatelier_mcp_app} />", source)

    def test_retry_control_does_not_collapse_on_narrow_viewports(self):
        source = FRAME.read_text(encoding="utf-8")
        self.assertEqual(source.count("shrink-0 whitespace-nowrap"), 2)

    def test_resource_load_can_be_cancelled_and_ignores_late_responses(self):
        source = FRAME.read_text(encoding="utf-8")
        self.assertIn("new AbortController()", source)
        self.assertIn("controller.signal", source)
        self.assertIn("generation !== requestGeneration", source)
        self.assertIn("on:click={cancelLoad}>Cancel", source)
        self.assertIn("onDestroy(() =>", source)

    def test_resource_load_has_a_bounded_timeout(self):
        source = FRAME.read_text(encoding="utf-8")
        self.assertIn("const appResourceTimeoutMs = 8_000", source)
        self.assertIn("The interactive tool result timed out.", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
