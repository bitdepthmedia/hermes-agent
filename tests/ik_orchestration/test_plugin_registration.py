from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ik_extensions.persona_orchestration.plugin import register
from hermes_cli.plugins import PluginManager


class FakeContext:
    def __init__(self) -> None:
        self.registrations = []
        self.hooks = {}

    def register_tool(self, **value: object) -> None:
        required = {"name", "toolset", "schema", "handler"}
        if not required <= set(value):
            raise TypeError("incomplete real plugin registration")
        self.registrations.append(value)

    def register_hook(self, name: str, callback: object) -> None:
        self.hooks[name] = callback


class PluginRegistrationTests(unittest.TestCase):
    def test_supported_plugin_api_registers_real_ingress_and_fail_closed_tool(self) -> None:
        ctx = FakeContext()
        register(ctx)
        registrations = {item["name"]: item for item in ctx.registrations}
        self.assertEqual(set(registrations), {"ik_route_intake", "ik_select_model_worker"})
        self.assertEqual(
            json.loads(registrations["ik_route_intake"]["handler"]({})),
            {"status": "BLOCKED_CONFIGURATION_REQUIRED"},
        )
        self.assertEqual(registrations["ik_route_intake"]["toolset"], "ik-orchestration")
        self.assertEqual(registrations["ik_select_model_worker"]["toolset"], "ik-orchestration")
        self.assertIn("pre_gateway_dispatch", ctx.hooks)

    def test_worker_tool_uses_bound_router_and_keeps_primary_for_tool_work(self) -> None:
        ctx = FakeContext()
        register(ctx)
        tool = {item["name"]: item for item in ctx.registrations}["ik_select_model_worker"]["handler"]
        router = Path(__file__).resolve().parents[2] / "ik_cells/ernie-router.json"
        with patch.dict(os.environ, {"IK_ROUTER_CONFIG": str(router)}):
            result = json.loads(tool({"task_boundary": "conversation", "needs_tools": True}))
        self.assertEqual(result["status"], "CLEAR")
        self.assertEqual(result["model_id"], "qwen38-27b-q4km")
        self.assertTrue(result["reasoning_enabled"])

    def test_worker_tool_fails_closed_without_bound_router(self) -> None:
        ctx = FakeContext()
        register(ctx)
        tool = {item["name"]: item for item in ctx.registrations}["ik_select_model_worker"]["handler"]
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(json.loads(tool({"task_boundary": "conversation"}))["status"], "BLOCKED_ROUTER_CONFIGURATION")

    def test_supported_plugin_wrapper_is_discoverable_from_bundled_root(self) -> None:
        root = Path(__file__).resolve().parents[2] / "plugins"
        manifests = PluginManager()._scan_directory(root, "bundled")
        matched = [item for item in manifests if item.name == "ik-persona-orchestration"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(Path(str(matched[0].path)), root / "ik-persona-orchestration")

    def test_ingress_hook_stops_unaddressed_group_bot_messages_before_dispatch(self) -> None:
        ctx = FakeContext()
        register(ctx)
        event = SimpleNamespace(
            text="model retry 3/5",
            source=SimpleNamespace(
                platform=SimpleNamespace(value="telegram"),
                chat_type="group",
                is_bot=True,
            ),
            raw_message=SimpleNamespace(
                text="model retry 3/5",
                caption=None,
                reply_to_message=None,
            ),
        )
        gateway = SimpleNamespace(
            config=SimpleNamespace(
                platforms={
                    "telegram": SimpleNamespace(
                        extra={"group_bot_messages": "mentions_only"}
                    )
                }
            ),
            adapters={},
        )

        result = ctx.hooks["pre_gateway_dispatch"](event=event, gateway=gateway)

        self.assertEqual(
            result,
            {
                "action": "skip",
                "reason": "telegram_group_bot_message_not_explicitly_addressed",
            },
        )


if __name__ == "__main__":
    unittest.main()
