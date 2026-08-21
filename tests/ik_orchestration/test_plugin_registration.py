from __future__ import annotations

import json
import unittest

from ik_extensions.persona_orchestration.plugin import register


class FakeContext:
    def __init__(self) -> None:
        self.registration = None

    def register_tool(self, **value: object) -> None:
        self.registration = value


class PluginRegistrationTests(unittest.TestCase):
    def test_supported_plugin_api_registers_disabled_overlay_tool_with_json_result(self) -> None:
        ctx = FakeContext()
        register(ctx)
        self.assertEqual(ctx.registration["name"], "ik_route_intake")
        self.assertEqual(json.loads(ctx.registration["handler"]()), {"status": "policy-evaluation-required"})


if __name__ == "__main__":
    unittest.main()
