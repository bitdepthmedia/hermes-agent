"""Supported Hermes plugin entrypoint for the IK orchestration overlay."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .envelope import Owner
from .ingress import IngressCoordinator, guard_telegram_group_bot_message
from .store import HandoffStore
from ik_extensions.model_workers.runtime_router import RouterRequest, load_router_config, prepare_worker_request


def register(ctx: object) -> None:
    register_tool = getattr(ctx, "register_tool", None)
    register_hook = getattr(ctx, "register_hook", None)
    if not callable(register_tool) or not callable(register_hook):
        raise RuntimeError("supported Hermes plugin API unavailable")
    coordinator: IngressCoordinator | None = None

    def configured() -> IngressCoordinator | None:
        nonlocal coordinator
        if coordinator is not None:
            return coordinator
        try:
            cell = Owner(os.environ["IK_CELL_ID"])
            home = Path(os.environ["HERMES_HOME"]).resolve()
        except (KeyError, ValueError):
            return None
        if cell not in {Owner.BERT, Owner.ERNIE} or home.is_symlink():
            return None
        store_path = home / "state/ik-handoff.sqlite"
        coordinator = IngressCoordinator(cell=cell, store=HandoffStore(store_path))
        return coordinator

    def route_tool(*_: object, **__: object) -> str:
        return json.dumps(
            {"status": "READY" if configured() is not None else "BLOCKED_CONFIGURATION_REQUIRED"},
            sort_keys=True,
        )

    def select_model_worker(arguments: object = None, **_: object) -> str:
        if not isinstance(arguments, dict):
            return json.dumps({"status": "BLOCKED_INVALID_REQUEST"}, sort_keys=True)
        path_value = os.environ.get("IK_ROUTER_CONFIG", "")
        try:
            path = Path(path_value).absolute()
            config = load_router_config(path)
            request = RouterRequest(
                task_boundary=str(arguments.get("task_boundary", "conversation")),
                bounded_specialist_task=bool(arguments.get("bounded_specialist_task", False)),
                pinned_model_id=(str(arguments["pinned_model_id"]) if arguments.get("pinned_model_id") else None),
                reasoning_enabled=bool(arguments.get("reasoning_enabled", True)),
                messages=(),
                needs_tools=bool(arguments.get("needs_tools", False)),
            )
            selected = prepare_worker_request(request, config)
        except Exception:
            return json.dumps({"status": "BLOCKED_ROUTER_CONFIGURATION"}, sort_keys=True)
        return json.dumps(
            {
                "status": "CLEAR",
                "model_id": selected.model_id,
                "runtime_model": selected.runtime_model,
                "reasoning_enabled": selected.reasoning_enabled,
                "selection_reason": selected.selection_reason,
            },
            sort_keys=True,
        )

    def ingress_hook(*, event: object, gateway: object, **_: object) -> dict[str, str]:
        group_bot_guard = guard_telegram_group_bot_message(event, gateway)
        if group_bot_guard is not None:
            return group_bot_guard
        active = configured()
        if active is None:
            return {"action": "rewrite", "text": "Cell ingress configuration is missing; no task was executed."}
        try:
            return active.handle(event, gateway)
        except Exception:
            return {"action": "rewrite", "text": "Cell ingress failed closed; no task was executed."}

    register_tool(
        name="ik_route_intake",
        toolset="ik-orchestration",
        schema={
            "name": "ik_route_intake",
            "description": "Check the durable persona routing ingress.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        handler=route_tool,
    )
    register_tool(
        name="ik_select_model_worker",
        toolset="ik-orchestration",
        schema={
            "name": "ik_select_model_worker",
            "description": "Select a verified model worker at a task boundary.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_boundary": {"type": "string"},
                    "bounded_specialist_task": {"type": "boolean"},
                    "pinned_model_id": {"type": "string"},
                    "reasoning_enabled": {"type": "boolean"},
                    "needs_tools": {"type": "boolean"},
                },
                "required": ["task_boundary"],
                "additionalProperties": False,
            },
        },
        handler=select_model_worker,
    )
    register_hook("pre_gateway_dispatch", ingress_hook)
