"""Shadow-mode routing adapter for the local Bert/Ernie shared control plane."""

from __future__ import annotations

import os
from pathlib import Path

from shared_core import ActionClass, SharedCore, TaskOwner
from shared_core.core import Task


class SharedCoreAdapter:
    """Records a routing decision without changing the gateway's current delivery path."""

    def __init__(self, core: SharedCore, *, primary: TaskOwner = TaskOwner.BERT, shadow_mode: bool = True):
        self.core = core
        self.primary = primary
        self.shadow_mode = shadow_mode
        self.shadow_events: list[dict[str, str]] = []
        self._delivery_owners: dict[str, TaskOwner] = {}

    def ingest(
        self,
        *,
        session_id: str,
        request: str,
        requested_owner: TaskOwner,
        action_class: ActionClass,
        offline: bool,
        contains_local_data: bool,
    ) -> Task:
        selected_owner = TaskOwner.ERNIE if offline or contains_local_data else requested_owner
        task = self.core.create_task(
            owner=selected_owner,
            session_id=session_id,
            request=request,
            action_class=action_class,
        )
        self._delivery_owners[task.id] = requested_owner
        self.shadow_events.append(
            {
                "task_id": task.id,
                "requested_owner": requested_owner.value,
                "selected_owner": selected_owner.value,
                "mode": "shadow" if self.shadow_mode else "active",
            }
        )
        return task

    def delivery_owner(self, task: Task) -> TaskOwner:
        """Return the current primary that owns the user-visible response."""
        return self._delivery_owners[task.id]


def adapter_from_environment() -> SharedCoreAdapter | None:
    """Create a profile-scoped adapter only when shadow mode is explicitly enabled."""
    if os.getenv("SHARED_CORE_SHADOW_MODE", "").lower() not in {"1", "true", "yes", "on"}:
        return None
    database_path = os.getenv("SHARED_CORE_DB", "").strip()
    if not database_path:
        return None
    primary = TaskOwner(os.getenv("SHARED_CORE_PRIMARY", TaskOwner.BERT.value).lower())
    Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return SharedCoreAdapter(SharedCore(Path(database_path).expanduser()), primary=primary, shadow_mode=True)
