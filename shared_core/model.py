"""Bert cloud-model selection that refuses unverified provider aliases."""

from __future__ import annotations


class BertModelTarget:
    display_name = "GPT-5.6 Terra Medium"

    def __init__(self, display_name: str = display_name, *, model_id: str):
        self.display_name = display_name
        self.model_id = model_id

    def preflight(self, available_model_ids: list[str]) -> str:
        if self.model_id not in available_model_ids:
            raise ValueError(f"{self.display_name} is not available from the configured provider")
        return self.model_id
