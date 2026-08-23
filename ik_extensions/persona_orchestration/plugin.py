"""Supported Hermes plugin entrypoint for the IK orchestration overlay."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .envelope import Owner
from .ingress import IngressCoordinator
from .store import HandoffStore


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

    def route_tool(**_: object) -> str:
        return json.dumps(
            {"status": "READY" if configured() is not None else "BLOCKED_CONFIGURATION_REQUIRED"},
            sort_keys=True,
        )

    def ingress_hook(*, event: object, gateway: object, **_: object) -> dict[str, str]:
        active = configured()
        if active is None:
            return {"action": "rewrite", "text": "Cell ingress configuration is missing; no task was executed."}
        try:
            return active.handle(event, gateway)
        except Exception:
            return {"action": "rewrite", "text": "Cell ingress failed closed; no task was executed."}

    register_tool(name="ik_route_intake", handler=route_tool)
    register_hook("pre_gateway_dispatch", ingress_hook)
