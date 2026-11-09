# SPDX-License-Identifier: BSD-3-Clause
"""Focused checks for the measured event and webhook compatibility contract."""

import ast
import hashlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROUTER = ROOT / "backend/open_webui/routers/events.py"
CONFIG = ROOT / "backend/open_webui/config.py"
MAIN = ROOT / "backend/open_webui/main.py"


class EventWebhookCompatibilityTests(unittest.TestCase):
    def test_five_measured_operations_require_an_administrator(self):
        tree = ast.parse(ROUTER.read_text())
        names = {
            "get_event_catalog",
            "get_event_webhooks_api",
            "create_event_webhook",
            "update_event_webhook",
            "delete_event_webhook_api",
        }
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name in names
        }
        self.assertEqual(set(functions), names)
        for function in functions.values():
            self.assertIn("get_admin_user", ast.unparse(function.args))

    def test_current_catalog_key_set_and_order_are_exact(self):
        tree = ast.parse(ROUTER.read_text())
        assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "EVENT_NAMES" for target in node.targets)
        )
        names = ast.literal_eval(assignment.value)
        self.assertEqual(len(names), 191)
        self.assertEqual(len(set(names)), 191)
        self.assertEqual(
            hashlib.sha256("\n".join(names).encode()).hexdigest(),
            "d0e152f39325014daa69268c0d33131492daf82164d4755fdcc6754422a63c10",
        )

    def test_forms_and_defaults_match_the_measured_openapi_contract(self):
        source = ROUTER.read_text()
        for fragment in (
            "class EventWebhookForm(BaseModel):",
            "url: str",
            "name: Optional[str] = None",
            "events: Optional[list[str]] = None",
            "targets: Optional[list[dict[str, str]]] = None",
            "enabled: bool = True",
            "class EventWebhookUpdateForm(BaseModel):",
            "enabled: Optional[bool] = None",
            '"events": form_data.events or ["*"]',
            '"name": form_data.name or "Webhook"',
        ):
            self.assertIn(fragment, source)

    def test_durable_config_and_root_mount_are_explicit(self):
        self.assertIn('"events.webhooks"', CONFIG.read_text())
        main = MAIN.read_text()
        self.assertIn("app.state.config.EVENT_WEBHOOKS = EVENT_WEBHOOKS", main)
        self.assertIn('app.include_router(events.router, prefix="/api/events")', main)

    def test_measured_success_and_failure_shapes_are_preserved(self):
        source = ROUTER.read_text()
        for fragment in (
            '"schema": "0.12.5"',
            'status_code=400, detail="The URL you provided is invalid. Please double-check and try again."',
            'status_code=404, detail="Webhook not found"',
            'return {"status": True}',
            '"id": str(uuid.uuid4())',
            '"created_at": now',
            '"updated_at": now',
        ):
            self.assertIn(fragment, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
