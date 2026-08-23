"""Loopback-only Ernie provider with Qwen3.8 request normalization."""

from __future__ import annotations

import os
import re
from typing import Any

from ik_extensions.model_workers.qwen38_adapter import adapt_qwen38_messages
from providers import register_provider
from providers.base import ProviderProfile


_ENDPOINT = os.environ.get("IK_MODEL_BASE_URL", "")
if not re.fullmatch(r"http://127\.0\.0\.1:(?:[1-9][0-9]{3,4})/v1", _ENDPOINT):
    raise RuntimeError("Ernie model provider requires an exact IPv4 loopback endpoint")
_PORT = int(_ENDPOINT.split(":", 2)[2].split("/", 1)[0])
if not 1024 <= _PORT <= 65535:
    raise RuntimeError("Ernie model provider requires an exact IPv4 loopback endpoint")


class ErnieLocalProfile(ProviderProfile):
    def prepare_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(adapt_qwen38_messages(messages, reasoning_enabled=True))


ik_ernie_local = ErnieLocalProfile(
    name="ik-ernie-local",
    display_name="Ernie local verified model worker",
    description="Loopback Qwen3.8 worker with typed approval and tool-history normalization",
    base_url=_ENDPOINT,
    models_url=_ENDPOINT.removesuffix("/v1") + "/api/tags",
    supports_health_check=False,
    fallback_models=("ik-qwen38-eval:31629f53165a",),
)

register_provider(ik_ernie_local)
