"""Tests for hermes-api-server toolset and API server tool availability."""
import asyncio
import hashlib
import os
import json
import sys
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from toolsets import resolve_toolset, get_toolset, validate_toolset


def _canonical_sha256(value):
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TestHermesApiServerToolset:
    """Tests for the hermes-api-server toolset definition."""

    def test_toolset_exists(self):
        ts = get_toolset("hermes-api-server")
        assert ts is not None

    def test_toolset_validates(self):
        assert validate_toolset("hermes-api-server")

    def test_toolset_includes_web_tools(self):
        tools = resolve_toolset("hermes-api-server")
        assert "web_search" in tools
        assert "web_extract" in tools

    def test_toolset_includes_core_tools(self):
        tools = resolve_toolset("hermes-api-server")
        expected = [
            "terminal", "process",
            "read_file", "write_file", "patch", "search_files",
            "vision_analyze", "image_generate",
            "execute_code", "delegate_task",
            "todo", "memory", "session_search", "cronjob",
        ]
        for tool in expected:
            assert tool in tools, f"Missing expected tool: {tool}"

    def test_toolset_includes_browser_tools(self):
        tools = resolve_toolset("hermes-api-server")
        for tool in ["browser_navigate", "browser_snapshot", "browser_click",
                      "browser_type", "browser_scroll", "browser_back",
                      "browser_press"]:
            assert tool in tools, f"Missing browser tool: {tool}"

    def test_toolset_includes_homeassistant_tools(self):
        tools = resolve_toolset("hermes-api-server")
        for tool in ["ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service"]:
            assert tool in tools, f"Missing HA tool: {tool}"

    def test_toolset_excludes_clarify(self):
        tools = resolve_toolset("hermes-api-server")
        assert "clarify" not in tools

    def test_toolset_excludes_send_message(self):
        tools = resolve_toolset("hermes-api-server")
        assert "send_message" not in tools

    def test_toolset_excludes_text_to_speech(self):
        tools = resolve_toolset("hermes-api-server")
        assert "text_to_speech" not in tools


class TestApiServerPlatformConfig:
    def test_platforms_dict_includes_api_server(self):
        from hermes_cli.tools_config import PLATFORMS
        assert "api_server" in PLATFORMS
        assert PLATFORMS["api_server"]["default_toolset"] == "hermes-api-server"


class TestApiServerAdapterToolset:
    @patch("gateway.platforms.api_server.AIOHTTP_AVAILABLE", True)
    def test_create_agent_reads_config_toolsets(self):
        """API server resolves toolsets from config like all other platforms."""
        from gateway.platforms.api_server import APIServerAdapter
        from gateway.config import PlatformConfig

        adapter = APIServerAdapter(PlatformConfig())

        fake_agent_cls = MagicMock()
        fake_run_agent = types.SimpleNamespace(AIAgent=fake_agent_cls)
        with patch("gateway.run._resolve_runtime_agent_kwargs") as mock_kwargs, \
             patch("gateway.run._resolve_gateway_model") as mock_model, \
             patch("gateway.run._load_gateway_config") as mock_config, \
             patch.dict(sys.modules, {"run_agent": fake_run_agent}):

            mock_kwargs.return_value = {"api_key": "test-key", "base_url": None,
                                        "provider": None, "api_mode": None,
                                        "command": None, "args": []}
            mock_model.return_value = "test/model"
            # No platform_toolsets override — should fall back to hermes-api-server default
            mock_config.return_value = {}
            fake_agent_cls.return_value = MagicMock()

            adapter._create_agent()

            fake_agent_cls.assert_called_once()
            call_kwargs = fake_agent_cls.call_args
            toolsets = call_kwargs.kwargs.get("enabled_toolsets")
            assert isinstance(toolsets, list)
            assert len(toolsets) > 0
            assert call_kwargs.kwargs.get("platform") == "api_server"

    @patch("gateway.platforms.api_server.AIOHTTP_AVAILABLE", True)
    def test_create_agent_respects_config_override(self):
        """User can override API server toolsets via platform_toolsets in config.yaml."""
        from gateway.platforms.api_server import APIServerAdapter
        from gateway.config import PlatformConfig

        adapter = APIServerAdapter(PlatformConfig())

        fake_agent_cls = MagicMock()
        fake_run_agent = types.SimpleNamespace(AIAgent=fake_agent_cls)
        with patch("gateway.run._resolve_runtime_agent_kwargs") as mock_kwargs, \
             patch("gateway.run._resolve_gateway_model") as mock_model, \
             patch("gateway.run._load_gateway_config") as mock_config, \
             patch.dict(sys.modules, {"run_agent": fake_run_agent}):

            mock_kwargs.return_value = {"api_key": "test-key", "base_url": None,
                                        "provider": None, "api_mode": None,
                                        "command": None, "args": []}
            mock_model.return_value = "test/model"
            # User overrides with just web and terminal
            mock_config.return_value = {
                "platform_toolsets": {"api_server": ["web", "terminal"]}
            }
            fake_agent_cls.return_value = MagicMock()

            adapter._create_agent()

            fake_agent_cls.assert_called_once()
            call_kwargs = fake_agent_cls.call_args
            toolsets = call_kwargs.kwargs.get("enabled_toolsets")
            assert sorted(toolsets) == ["terminal", "web"]

    @patch("gateway.platforms.api_server.AIOHTTP_AVAILABLE", True)
    def test_read_only_agent_cannot_inherit_configured_api_toolsets(self):
        from gateway.platforms.api_server import APIServerAdapter
        from gateway.config import PlatformConfig

        adapter = APIServerAdapter(PlatformConfig())
        fake_agent = MagicMock()
        fake_agent.tools = []
        fake_agent.valid_tool_names = set()
        fake_agent_cls = MagicMock(return_value=fake_agent)
        fake_run_agent = types.SimpleNamespace(AIAgent=fake_agent_cls)

        with patch(
            "gateway.run._resolve_runtime_agent_kwargs",
            return_value={
                "api_key": "test-key",
                "base_url": None,
                "provider": None,
                "api_mode": None,
                "command": None,
                "args": [],
                "enabled_toolsets": ["terminal", "web"],
                "skip_memory": False,
                "persist_session": True,
            },
        ), patch(
            "gateway.run._resolve_gateway_model",
            return_value="test/model",
        ), patch.dict(sys.modules, {"run_agent": fake_run_agent}):
            created = adapter._create_read_only_agent(
                ephemeral_system_prompt="bounded receipts",
                max_tokens=400,
            )

        assert created is fake_agent
        kwargs = fake_agent_cls.call_args.kwargs
        assert kwargs["enabled_toolsets"] == []
        assert kwargs["skip_memory"] is True
        assert kwargs["skip_context_files"] is True
        assert kwargs["persist_session"] is False

    @patch("gateway.platforms.api_server.AIOHTTP_AVAILABLE", True)
    def test_status_receipts_page_through_complete_seven_day_window(self):
        from gateway.platforms import api_server
        from gateway.platforms.api_server import APIServerAdapter
        from gateway.config import PlatformConfig

        adapter = APIServerAdapter(PlatformConfig())
        now = datetime(2026, 7, 18, 13, 5, tzinfo=UTC)
        rows = [
            {
                "id": "recent-1",
                "source": "telegram",
                "started_at": (now - timedelta(days=1)).timestamp(),
                "ended_at": None,
                "end_reason": None,
                "message_count": 4,
                "tool_call_count": 0,
            },
            {
                "id": "recent-2",
                "source": "cron",
                "started_at": (now - timedelta(days=6)).timestamp(),
                "ended_at": now.timestamp(),
                "end_reason": "completed",
                "message_count": 2,
                "tool_call_count": 0,
            },
            {
                "id": "older",
                "source": "cli",
                "started_at": (now - timedelta(days=8)).timestamp(),
                "ended_at": now.timestamp(),
                "end_reason": "completed",
                "message_count": 1,
                "tool_call_count": 0,
            },
        ]

        class FakeSessionDB:
            def session_count(self):
                return len(rows)

            def search_sessions(self, source=None, limit=20, offset=0):
                return rows[offset:offset + limit]

        cron_jobs = [
            {
                "id": "recent-job",
                "name": "recent",
                "last_run_at": (now - timedelta(days=2)).isoformat(),
            },
            {
                "id": "old-job",
                "name": "old",
                "last_run_at": (now - timedelta(days=9)).isoformat(),
            },
        ]
        with patch.object(api_server, "READ_ONLY_SESSION_PAGE_SIZE", 1), \
             patch.object(adapter, "_ensure_session_db", return_value=FakeSessionDB()), \
             patch("cron.jobs.list_jobs", return_value=cron_jobs):
            receipts = adapter._collect_read_only_status_receipts(now=now)

        sessions = receipts["items"][0]
        assert sessions["kind"] == "session_db_metadata"
        assert [row["id"] for row in sessions["records"]] == ["recent-1", "recent-2"]
        assert sessions["pagination"] == {
            "page_size": 1,
            "max_pages": api_server.READ_ONLY_SESSION_MAX_PAGES,
            "pages_fetched": 3,
            "rows_scanned": 3,
            "rows_in_window": 2,
            "total_rows": 3,
            "complete": True,
            "truncated": False,
        }
        cron = receipts["items"][1]
        assert cron["pagination"]["rows_in_window"] == 1
        assert receipts["derived_status"]["status"] == "NO_PENDING_WORK"
        assert receipts["coverage"]["pending_record_refs"] == []

    @patch("gateway.platforms.api_server.AIOHTTP_AVAILABLE", True)
    def test_status_receipt_keeps_older_unresolved_session(self):
        from gateway.platforms.api_server import APIServerAdapter
        from gateway.config import PlatformConfig

        adapter = APIServerAdapter(
            PlatformConfig(extra={"key": "x", "host": "127.0.0.1", "port": 8643})
        )
        now = datetime(2026, 7, 18, tzinfo=UTC)
        rows = [{
            "id": "older-open",
            "source": "cron",
            "started_at": (now - timedelta(days=30)).timestamp(),
            "ended_at": None,
        }]
        db = MagicMock()
        db.session_count.return_value = 1
        db.search_sessions.return_value = rows
        with patch.object(adapter, "_ensure_session_db", return_value=db), \
             patch("cron.jobs.list_jobs", return_value=[]):
            receipts = adapter._collect_read_only_status_receipts(now=now)
        assert receipts["items"][0]["records"][0]["id"] == "older-open"
        assert receipts["derived_status"] == {
            "status": "NO_PENDING_WORK",
            "evidence_refs": ["coverage:complete"],
        }

    @patch("gateway.platforms.api_server.AIOHTTP_AVAILABLE", True)
    def test_read_only_endpoint_attests_zero_tools_and_hash_bindings(self):
        from gateway.platforms.api_server import APIServerAdapter
        from gateway.config import PlatformConfig

        adapter = APIServerAdapter(
            PlatformConfig(extra={"key": "test-secret", "host": "127.0.0.1", "port": 8643})
        )
        payload = {
            "purpose": "status",
            "input": "Return strict status JSON.",
            "max_tokens": 400,
        }
        source_receipts = {
            "purpose": "status",
            "items": [{"kind": "session_db_metadata", "pagination": {"complete": True}}],
        }
        fake_agent = MagicMock()
        fake_agent.tools = []
        fake_agent.valid_tool_names = set()
        fake_agent.run_conversation.return_value = {
            "final_response": '{"status":"NO_PENDING_WORK"}',
            "messages": [
                {"role": "user", "content": payload["input"]},
                {"role": "assistant", "content": '{"status":"NO_PENDING_WORK"}'},
            ],
        }
        request = types.SimpleNamespace(
            remote="127.0.0.1",
            headers={"Authorization": "Bearer test-secret"},
            json=AsyncMock(return_value=payload),
        )

        with patch.object(
            adapter,
            "_collect_read_only_status_receipts",
            return_value=source_receipts,
        ), patch.object(
            adapter,
            "_create_read_only_agent",
            return_value=fake_agent,
        ):
            response = asyncio.run(adapter._handle_orchestrator_read_only(request))

        assert response.status == 200
        body = json.loads(response.text)
        attestation = body["attestation"]
        assert attestation["mode"] == "no_tools"
        assert attestation["enabled_toolsets"] == []
        assert attestation["tool_names"] == []
        assert attestation["tool_calls"] == 0
        assert attestation["request_sha256"] == _canonical_sha256(payload)
        assert attestation["input_sha256"] == hashlib.sha256(
            payload["input"].encode("utf-8")
        ).hexdigest()
        assert attestation["output_sha256"] == hashlib.sha256(
            body["content"].encode("utf-8")
        ).hexdigest()
        assert attestation["source_receipts_sha256"] == _canonical_sha256(
            source_receipts
        )

    @patch("gateway.platforms.api_server.AIOHTTP_AVAILABLE", True)
    def test_review_endpoint_rejects_tampered_caller_receipt(self):
        from gateway.platforms.api_server import APIServerAdapter
        from gateway.config import PlatformConfig

        adapter = APIServerAdapter(
            PlatformConfig(extra={"key": "test-secret", "host": "127.0.0.1", "port": 8643})
        )
        payload = {
            "purpose": "review",
            "input": "Review this evidence.",
            "max_tokens": 400,
            "source_receipt": {
                "content": {"summary": "bounded fixed-GET result"},
                "sha256": "0" * 64,
            },
        }
        request = types.SimpleNamespace(
            remote="127.0.0.1",
            headers={"Authorization": "Bearer test-secret"},
            json=AsyncMock(return_value=payload),
        )

        response = asyncio.run(adapter._handle_orchestrator_read_only(request))

        assert response.status == 400
        assert "receipt hash" in response.text.lower()
