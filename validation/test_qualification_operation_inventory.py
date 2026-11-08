# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 Gaël Duval

import unittest

from validation.qualification.operation_inventory import (
    operations_from_openapi,
    route_domain,
)


class OperationInventoryTests(unittest.TestCase):
    def test_operations_are_sorted_and_ignore_non_method_fields(self):
        contract = {
            "paths": {
                "/z": {"post": {}, "parameters": []},
                "/a": {"get": {}, "x-extension": {}},
            }
        }
        self.assertEqual(operations_from_openapi(contract), ["GET /a", "POST /z"])

    def test_missing_or_invalid_paths_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "no paths"):
            operations_from_openapi({})
        with self.assertRaisesRegex(ValueError, "invalid OpenAPI path item"):
            operations_from_openapi({"paths": {"/broken": []}})

    def test_route_domains_are_deterministic(self):
        self.assertEqual(route_domain("GET /api/v1/auths/"), "identity")
        self.assertEqual(route_domain("POST /api/chat/completions"), "chat")
        self.assertEqual(route_domain("GET /api/v1/terminals/"), "extensions")
        self.assertEqual(route_domain("GET /health"), "core")


if __name__ == "__main__":
    unittest.main()
