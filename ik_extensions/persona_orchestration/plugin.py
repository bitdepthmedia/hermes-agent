"""Supported Hermes plugin entrypoint for the IK orchestration overlay."""

from __future__ import annotations

import json


def register(ctx: object) -> None:
    register_tool = getattr(ctx, "register_tool", None)
    if not callable(register_tool):
        raise RuntimeError("supported Hermes plugin API unavailable")
    register_tool(name="ik_route_intake", handler=lambda **_: json.dumps({"status": "policy-evaluation-required"}, sort_keys=True))
