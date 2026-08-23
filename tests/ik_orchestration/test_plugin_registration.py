from __future__ import annotations

import json
import unittest

from ik_extensions.persona_orchestration.plugin import register


class FakeContext:
    def __init__(self) -> None:
        self.registration = None
        self.hooks = {}

    def register_tool(self, **value: object) -> None:
        self.registration = value

    def register_hook(self, name: str, callback: object) -> None:
        self.hooks[name] = callback


class PluginRegistrationTests(unittest.TestCase):
    def test_supported_plugin_api_registers_real_ingress_and_fail_closed_tool(self) -> None:
        ctx = FakeContext()
        register(ctx)
        self.assertEqual(ctx.registration["name"], "ik_route_intake")
        self.assertEqual(json.loads(ctx.registration["handler"]()), {"status": "BLOCKED_CONFIGURATION_REQUIRED"})
        self.assertIn("pre_gateway_dispatch", ctx.hooks)


if __name__ == "__main__":
    unittest.main()
