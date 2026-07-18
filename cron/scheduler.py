"""
Cron job scheduler - executes due jobs.

Provides tick() which checks for due jobs and runs them. The gateway
calls this every 60 seconds from a background thread.

Uses a file-based lock (~/.hermes/cron/.tick.lock) so only one tick
runs at a time if multiple processes overlap.
"""

import asyncio
import concurrent.futures
import json
import logging
import os
import signal
import subprocess
import sys
import queue
import threading

# fcntl is Unix-only; on Windows use msvcrt for file locking
try:
    import fcntl
except ImportError:
    fcntl = None
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports BEFORE repo-level imports.
# Without this, standalone invocations (e.g. after `hermes update` reloads
# the module) fail with ModuleNotFoundError for hermes_time et al.
sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_constants import get_hermes_home
from hermes_cli.config import load_config
from hermes_time import now as _hermes_now

logger = logging.getLogger(__name__)


class DeliveryFailure(str):
    """Failure text carrying whether another send is provably safe."""

    def __new__(cls, message: str, *, definitive: bool):
        value = super().__new__(cls, message)
        value.definitive = definitive
        return value


def _standalone_send_with_deadline(factory, timeout: float = 30):
    """Run a possibly blocking async adapter behind a bounded daemon boundary."""
    results: queue.Queue = queue.Queue(maxsize=1)

    def runner():
        try:
            results.put((True, asyncio.run(factory())))
        except BaseException as exc:
            results.put((False, exc))

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        ok, value = results.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError("standalone delivery deadline exceeded") from exc
    if not ok:
        raise value
    return value

# Valid delivery platforms — used to validate user-supplied platform names
# in cron delivery targets, preventing env var enumeration via crafted names.
_KNOWN_DELIVERY_PLATFORMS = frozenset({
    "telegram", "discord", "slack", "whatsapp", "signal",
    "matrix", "mattermost", "homeassistant", "dingtalk", "feishu",
    "wecom", "sms", "email", "webhook",
})

from cron.jobs import (
    advance_next_run,
    get_due_jobs,
    get_job,
    mark_job_run,
    save_job_output,
    trigger_job,
)

# Sentinel: when a cron agent has nothing new to report, it can start its
# response with this marker to suppress delivery.  Output is still saved
# locally for audit.
SILENT_MARKER = "[SILENT]"

# Resolve Hermes home directory (respects HERMES_HOME override)
_hermes_home = get_hermes_home()

# File-based lock prevents concurrent ticks from gateway + daemon + systemd timer
_LOCK_DIR = _hermes_home / "cron"
_LOCK_FILE = _LOCK_DIR / ".tick.lock"


def _resolve_origin(job: dict) -> Optional[dict]:
    """Extract origin info from a job, preserving any extra routing metadata."""
    origin = job.get("origin")
    if not origin:
        return None
    platform = origin.get("platform")
    chat_id = origin.get("chat_id")
    if platform and chat_id:
        return origin
    return None


def _resolve_delivery_target(job: dict) -> Optional[dict]:
    """Resolve the concrete auto-delivery target for a cron job, if any."""
    deliver = job.get("deliver", "local")
    origin = _resolve_origin(job)

    if deliver == "local":
        return None

    if deliver == "origin":
        if origin:
            return {
                "platform": origin["platform"],
                "chat_id": str(origin["chat_id"]),
                "thread_id": origin.get("thread_id"),
            }
        # Origin missing (e.g. job created via API/script) — try each
        # platform's home channel as a fallback instead of silently dropping.
        for platform_name in ("matrix", "telegram", "discord", "slack"):
            chat_id = os.getenv(f"{platform_name.upper()}_HOME_CHANNEL", "")
            if chat_id:
                logger.info(
                    "Job '%s' has deliver=origin but no origin; falling back to %s home channel",
                    job.get("name", job.get("id", "?")),
                    platform_name,
                )
                return {
                    "platform": platform_name,
                    "chat_id": chat_id,
                    "thread_id": None,
                }
        return None

    if ":" in deliver:
        platform_name, rest = deliver.split(":", 1)
        platform_key = platform_name.lower()

        from tools.send_message_tool import _parse_target_ref

        parsed_chat_id, parsed_thread_id, is_explicit = _parse_target_ref(platform_key, rest)
        if is_explicit:
            chat_id, thread_id = parsed_chat_id, parsed_thread_id
        else:
            chat_id, thread_id = rest, None

        # Resolve human-friendly labels like "Alice (dm)" to real IDs.
        try:
            from gateway.channel_directory import resolve_channel_name
            resolved = resolve_channel_name(platform_key, chat_id)
            if resolved:
                parsed_chat_id, parsed_thread_id, resolved_is_explicit = _parse_target_ref(platform_key, resolved)
                if resolved_is_explicit:
                    chat_id, thread_id = parsed_chat_id, parsed_thread_id
                else:
                    chat_id = resolved
        except Exception:
            pass

        return {
            "platform": platform_name,
            "chat_id": chat_id,
            "thread_id": thread_id,
        }

    platform_name = deliver
    if origin and origin.get("platform") == platform_name:
        return {
            "platform": platform_name,
            "chat_id": str(origin["chat_id"]),
            "thread_id": origin.get("thread_id"),
        }

    if platform_name.lower() not in _KNOWN_DELIVERY_PLATFORMS:
        return None
    chat_id = os.getenv(f"{platform_name.upper()}_HOME_CHANNEL", "")
    if not chat_id:
        return None

    return {
        "platform": platform_name,
        "chat_id": chat_id,
        "thread_id": None,
    }


# Media extension sets — keep in sync with gateway/platforms/base.py:_process_message_background
_AUDIO_EXTS = frozenset({'.ogg', '.opus', '.mp3', '.wav', '.m4a'})
_VIDEO_EXTS = frozenset({'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'})
_IMAGE_EXTS = frozenset({'.jpg', '.jpeg', '.png', '.webp', '.gif'})


def _send_media_via_adapter(adapter, chat_id: str, media_files: list, metadata: dict | None, loop, job: dict) -> None:
    """Send extracted MEDIA files as native platform attachments via a live adapter.

    Routes each file to the appropriate adapter method (send_voice, send_image_file,
    send_video, send_document) based on file extension — mirroring the routing logic
    in ``BasePlatformAdapter._process_message_background``.
    """
    from pathlib import Path

    for media_path, _is_voice in media_files:
        try:
            ext = Path(media_path).suffix.lower()
            if ext in _AUDIO_EXTS:
                coro = adapter.send_voice(chat_id=chat_id, audio_path=media_path, metadata=metadata)
            elif ext in _VIDEO_EXTS:
                coro = adapter.send_video(chat_id=chat_id, video_path=media_path, metadata=metadata)
            elif ext in _IMAGE_EXTS:
                coro = adapter.send_image_file(chat_id=chat_id, image_path=media_path, metadata=metadata)
            else:
                coro = adapter.send_document(chat_id=chat_id, file_path=media_path, metadata=metadata)

            future = asyncio.run_coroutine_threadsafe(coro, loop)
            result = future.result(timeout=30)
            if result and not getattr(result, "success", True):
                logger.warning(
                    "Job '%s': media send failed for %s: %s",
                    job.get("id", "?"), media_path, getattr(result, "error", "unknown"),
                )
        except Exception as e:
            logger.warning("Job '%s': failed to send media %s: %s", job.get("id", "?"), media_path, e)


def _deliver_result(job: dict, content: str, adapters=None, loop=None) -> Optional[str]:
    """
    Deliver job output to the configured target (origin chat, specific platform, etc.).

    When ``adapters`` and ``loop`` are provided (gateway is running), tries to
    use the live adapter first — this supports E2EE rooms (e.g. Matrix) where
    the standalone HTTP path cannot encrypt.  Falls back to standalone send if
    the adapter path fails or is unavailable.

    Returns None on success, or an error string on failure.
    """
    target = _resolve_delivery_target(job)
    if not target:
        if job.get("deliver", "local") != "local":
            msg = f"no delivery target resolved for deliver={job.get('deliver', 'local')}"
            logger.warning("Job '%s': %s", job["id"], msg)
            return DeliveryFailure(msg, definitive=True)
        return None  # local-only jobs don't deliver — not a failure

    platform_name = target["platform"]
    chat_id = target["chat_id"]
    thread_id = target.get("thread_id")

    from tools.send_message_tool import _send_to_platform
    from gateway.config import load_gateway_config, Platform

    platform_map = {
        "telegram": Platform.TELEGRAM,
        "discord": Platform.DISCORD,
        "slack": Platform.SLACK,
        "whatsapp": Platform.WHATSAPP,
        "signal": Platform.SIGNAL,
        "matrix": Platform.MATRIX,
        "mattermost": Platform.MATTERMOST,
        "homeassistant": Platform.HOMEASSISTANT,
        "dingtalk": Platform.DINGTALK,
        "feishu": Platform.FEISHU,
        "wecom": Platform.WECOM,
        "email": Platform.EMAIL,
        "sms": Platform.SMS,
    }
    platform = platform_map.get(platform_name.lower())
    if not platform:
        msg = f"unknown platform '{platform_name}'"
        logger.warning("Job '%s': %s", job["id"], msg)
        return DeliveryFailure(msg, definitive=True)

    try:
        config = load_gateway_config()
    except Exception as e:
        msg = f"failed to load gateway config: {e}"
        logger.error("Job '%s': %s", job["id"], msg)
        return DeliveryFailure(msg, definitive=True)

    pconfig = config.platforms.get(platform)
    if not pconfig or not pconfig.enabled:
        msg = f"platform '{platform_name}' not configured/enabled"
        logger.warning("Job '%s': %s", job["id"], msg)
        return DeliveryFailure(msg, definitive=True)

    # Optionally wrap the content with a header/footer so the user knows this
    # is a cron delivery.  Wrapping is on by default; set cron.wrap_response: false
    # in config.yaml for clean output.
    if "wrap_response" in job:
        wrap_response = bool(job.get("wrap_response"))
    else:
        wrap_response = True
        try:
            user_cfg = load_config()
            wrap_response = user_cfg.get("cron", {}).get("wrap_response", True)
        except Exception:
            pass

    if wrap_response:
        task_name = job.get("name", job["id"])
        delivery_content = (
            f"Cronjob Response: {task_name}\n"
            f"-------------\n\n"
            f"{content}\n\n"
            f"Note: The agent cannot see this message, and therefore cannot respond to it."
        )
    else:
        delivery_content = content

    # Extract MEDIA: tags so attachments are forwarded as files, not raw text
    from gateway.platforms.base import BasePlatformAdapter
    media_files, cleaned_delivery_content = BasePlatformAdapter.extract_media(delivery_content)

    # Prefer the live adapter when the gateway is running — this supports E2EE
    # rooms (e.g. Matrix) where the standalone HTTP path cannot encrypt.
    runtime_adapter = (adapters or {}).get(platform)
    if runtime_adapter is not None and loop is not None and getattr(loop, "is_running", lambda: False)():
        send_metadata = {"thread_id": thread_id} if thread_id else None
        try:
            # Send cleaned text (MEDIA tags stripped) — not the raw content
            text_to_send = cleaned_delivery_content.strip()
            adapter_ok = True
            if text_to_send:
                future = asyncio.run_coroutine_threadsafe(
                    runtime_adapter.send(chat_id, text_to_send, metadata=send_metadata),
                    loop,
                )
                send_result = future.result(timeout=60)
                if send_result and not getattr(send_result, "success", True):
                    err = getattr(send_result, "error", "unknown")
                    lowered_error = str(err).lower()
                    if (
                        "timeout" in lowered_error
                        or "timed out" in lowered_error
                        or "deadline" in lowered_error
                    ):
                        msg = (
                            f"delivery to {platform_name}:{chat_id} returned an "
                            "ambiguous live adapter timeout; not retrying to avoid "
                            "duplicate delivery"
                        )
                        logger.error("Job '%s': %s", job["id"], msg)
                        return DeliveryFailure(msg, definitive=False)
                    definitive = bool(getattr(send_result, "definitive_failure", False))
                    if not definitive:
                        return DeliveryFailure(
                            f"ambiguous live adapter failure: {err}",
                            definitive=False,
                        )
                    logger.warning(
                        "Job '%s': definitive live adapter failure for %s:%s; using standalone",
                        job["id"], platform_name, chat_id,
                    )
                    adapter_ok = False

            # Send extracted media files as native attachments via the live adapter
            if adapter_ok and media_files:
                _send_media_via_adapter(runtime_adapter, chat_id, media_files, send_metadata, loop, job)

            if adapter_ok:
                logger.info("Job '%s': delivered to %s:%s via live adapter", job["id"], platform_name, chat_id)
                return None
        except concurrent.futures.TimeoutError:
            msg = (
                f"delivery to {platform_name}:{chat_id} timed out via live adapter; "
                "not retrying to avoid duplicate delivery"
            )
            logger.error("Job '%s': %s", job["id"], msg)
            return DeliveryFailure(msg, definitive=False)
        except Exception as e:
            return DeliveryFailure(
                f"ambiguous live adapter exception: {e}",
                definitive=False,
            )

    # Standalone path: run the async send in a fresh event loop (safe from any thread)
    try:
        result = _standalone_send_with_deadline(
            lambda: _send_to_platform(
                platform, pconfig, chat_id, cleaned_delivery_content,
                thread_id=thread_id, media_files=media_files
            )
        )
    except TimeoutError:
        msg = (
            f"delivery to {platform_name}:{chat_id} returned an ambiguous "
            "standalone timeout; not retrying to avoid duplicate delivery"
        )
        logger.error("Job '%s': %s", job["id"], msg)
        return DeliveryFailure(msg, definitive=False)
    except Exception as e:
        msg = f"delivery to {platform_name}:{chat_id} failed: {e}"
        logger.error("Job '%s': %s", job["id"], msg)
        return DeliveryFailure(msg, definitive=False)

    if result and result.get("error"):
        msg = f"delivery error: {result['error']}"
        logger.error("Job '%s': %s", job["id"], msg)
        return DeliveryFailure(msg, definitive=False)

    logger.info("Job '%s': delivered to %s:%s", job["id"], platform_name, chat_id)
    return None


_SCRIPT_TIMEOUT = 120  # seconds
_DIRECT_CRON_TOOLS = {"call_orchestrator", "daily_goal_coordinator"}
_DAILY_GOAL_DIRECT_TIMEOUT = 300.0


def _format_job_success_output(job: dict, prompt: str, final_response: str) -> str:
    logged_response = final_response if final_response else "(No response generated)"
    return f"""# Cron Job: {job["name"]}

**Job ID:** {job["id"]}
**Run Time:** {_hermes_now().strftime('%Y-%m-%d %H:%M:%S')}
**Schedule:** {job.get('schedule_display', 'N/A')}

## Prompt

{prompt}

## Response

{logged_response}
"""


def _run_daily_goal_direct_subprocess(
    tool_args: dict,
    timeout_seconds: float,
    *,
    command: list[str] | None = None,
) -> str:
    """Run the coordinator in a killable process group with a hard deadline."""
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise ValueError("daily goal timeout_seconds must be positive")
    if command is None:
        command = [
            sys.executable,
            "-m",
            "tools.daily_goal_coordinator_tool",
            "--cron-json",
            json.dumps(tool_args, sort_keys=True, separators=(",", ":")),
        ]
    process = subprocess.Popen(
        command,
        cwd=str(Path(__file__).resolve().parent.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=float(timeout_seconds))
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            process.communicate()
        raise TimeoutError(
            "daily_goal_coordinator exceeded its direct-tool deadline and "
            "its process group was terminated"
        )
    if process.returncode != 0:
        detail = (stderr or stdout or "subprocess failed").strip()[:1000]
        raise RuntimeError(
            f"daily_goal_coordinator subprocess failed: {detail}"
        )
    return stdout.strip()


def _run_direct_cron_tool(job: dict) -> Optional[str]:
    """Run a tightly allowlisted tool-backed cron job without a model hop."""
    direct_tool = job.get("direct_tool")
    if not direct_tool:
        return None

    if isinstance(direct_tool, str):
        tool_name = direct_tool
        tool_args = job.get("direct_tool_args") or {}
    elif isinstance(direct_tool, dict):
        tool_name = str(direct_tool.get("name") or "")
        tool_args = direct_tool.get("args") or {}
    else:
        raise ValueError("direct_tool must be a string or object")

    if tool_name not in _DIRECT_CRON_TOOLS:
        raise ValueError(f"Unsupported direct cron tool: {tool_name}")
    if not isinstance(tool_args, dict):
        raise ValueError("direct_tool args must be an object")

    if tool_name == "daily_goal_coordinator":
        job.pop("_daily_goal_cycle_id", None)
        job.pop("_daily_goal_dry_run", None)
        job.pop("_daily_goal_suppress_delivery", None)
        job.pop("_daily_goal_delivery_kind", None)

    if tool_name == "call_orchestrator":
        import tools.call_orchestrator_tool  # noqa: F401 - registers the tool

    if tool_name == "daily_goal_coordinator":
        raw = _run_daily_goal_direct_subprocess(
            tool_args,
            float(
                job.get(
                    "timeout_seconds",
                    _DAILY_GOAL_DIRECT_TIMEOUT,
                )
            ),
        )
    else:
        from tools.registry import registry

        raw = registry.dispatch(tool_name, tool_args)
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        if tool_name == "daily_goal_coordinator":
            raise ValueError(
                "daily_goal_coordinator returned a malformed result"
            )
        return str(raw).strip()

    if not isinstance(data, dict):
        if tool_name == "daily_goal_coordinator":
            raise ValueError(
                "daily_goal_coordinator returned a malformed result"
            )
        return str(raw).strip()

    if data.get("success") is True:
        if tool_name == "daily_goal_coordinator":
            content = data.get("content")
            cycle_id = data.get("cycle_id")
            if not isinstance(content, str) or not isinstance(cycle_id, str):
                raise ValueError(
                    "daily_goal_coordinator returned a malformed result"
                )
            if not cycle_id:
                raise ValueError(
                    "daily_goal_coordinator returned a malformed result"
                )
            job["_daily_goal_cycle_id"] = cycle_id
            job["_daily_goal_dry_run"] = bool(
                data.get("dry_run", tool_args.get("dry_run", False))
            )
            job["_daily_goal_suppress_delivery"] = bool(
                data.get("suppress_delivery", False)
            ) or job["_daily_goal_dry_run"]
            job["_daily_goal_delivery_kind"] = str(
                data.get("delivery_kind") or "original"
            )
        return str(data.get("content") or raw).strip()

    error = str(data.get("error") or raw).strip()
    if tool_name == "call_orchestrator":
        return f"ORCHESTRATOR_HEARTBEAT_FAILED {error}"
    if tool_name == "daily_goal_coordinator":
        raise ValueError(f"daily_goal_coordinator failed: {error}")
    return error


def _run_job_script(script_path: str) -> tuple[bool, str]:
    """Execute a cron job's data-collection script and capture its output.

    Scripts must reside within HERMES_HOME/scripts/.  Both relative and
    absolute paths are resolved and validated against this directory to
    prevent arbitrary script execution via path traversal or absolute
    path injection.

    Args:
        script_path: Path to a Python script.  Relative paths are resolved
            against HERMES_HOME/scripts/.  Absolute and ~-prefixed paths
            are also validated to ensure they stay within the scripts dir.

    Returns:
        (success, output) — on failure *output* contains the error message so the
        LLM can report the problem to the user.
    """
    from hermes_constants import get_hermes_home

    scripts_dir = get_hermes_home() / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    scripts_dir_resolved = scripts_dir.resolve()

    raw = Path(script_path).expanduser()
    if raw.is_absolute():
        path = raw.resolve()
    else:
        path = (scripts_dir / raw).resolve()

    # Guard against path traversal, absolute path injection, and symlink
    # escape — scripts MUST reside within HERMES_HOME/scripts/.
    try:
        path.relative_to(scripts_dir_resolved)
    except ValueError:
        return False, (
            f"Blocked: script path resolves outside the scripts directory "
            f"({scripts_dir_resolved}): {script_path!r}"
        )

    if not path.exists():
        return False, f"Script not found: {path}"
    if not path.is_file():
        return False, f"Script path is not a file: {path}"

    try:
        result = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True,
            text=True,
            timeout=_SCRIPT_TIMEOUT,
            cwd=str(path.parent),
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode != 0:
            parts = [f"Script exited with code {result.returncode}"]
            if stderr:
                parts.append(f"stderr:\n{stderr}")
            if stdout:
                parts.append(f"stdout:\n{stdout}")
            return False, "\n".join(parts)

        # Redact any secrets that may appear in script output before
        # they are injected into the LLM prompt context.
        try:
            from agent.redact import redact_sensitive_text
            stdout = redact_sensitive_text(stdout)
        except Exception:
            pass
        return True, stdout

    except subprocess.TimeoutExpired:
        return False, f"Script timed out after {_SCRIPT_TIMEOUT}s: {path}"
    except Exception as exc:
        return False, f"Script execution failed: {exc}"


def _build_job_prompt(job: dict) -> str:
    """Build the effective prompt for a cron job, optionally loading one or more skills first."""
    prompt = job.get("prompt", "")
    skills = job.get("skills")

    # Run data-collection script if configured, inject output as context.
    script_path = job.get("script")
    if script_path:
        success, script_output = _run_job_script(script_path)
        if success:
            if script_output:
                prompt = (
                    "## Script Output\n"
                    "The following data was collected by a pre-run script. "
                    "Use it as context for your analysis.\n\n"
                    f"```\n{script_output}\n```\n\n"
                    f"{prompt}"
                )
            else:
                prompt = (
                    "[Script ran successfully but produced no output.]\n\n"
                    f"{prompt}"
                )
        else:
            prompt = (
                "## Script Error\n"
                "The data-collection script failed. Report this to the user.\n\n"
                f"```\n{script_output}\n```\n\n"
                f"{prompt}"
            )

    # Always prepend cron execution guidance so the agent knows how
    # delivery works and can suppress delivery when appropriate.
    cron_hint = (
        "[SYSTEM: You are running as a scheduled cron job. "
        "DELIVERY: Your final response will be automatically delivered "
        "to the user — do NOT use send_message or try to deliver "
        "the output yourself. Just produce your report/output as your "
        "final response and the system handles the rest. "
        "SILENT: If there is genuinely nothing new to report, respond "
        "with exactly \"[SILENT]\" (nothing else) to suppress delivery. "
        "Never combine [SILENT] with content — either report your "
        "findings normally, or say [SILENT] and nothing more.]\n\n"
    )
    prompt = cron_hint + prompt
    if skills is None:
        legacy = job.get("skill")
        skills = [legacy] if legacy else []

    skill_names = [str(name).strip() for name in skills if str(name).strip()]
    if not skill_names:
        return prompt

    from tools.skills_tool import skill_view

    parts = []
    skipped: list[str] = []
    for skill_name in skill_names:
        loaded = json.loads(skill_view(skill_name))
        if not loaded.get("success"):
            error = loaded.get("error") or f"Failed to load skill '{skill_name}'"
            logger.warning("Cron job '%s': skill not found, skipping — %s", job.get("name", job.get("id")), error)
            skipped.append(skill_name)
            continue

        content = str(loaded.get("content") or "").strip()
        if parts:
            parts.append("")
        parts.extend(
            [
                f'[SYSTEM: The user has invoked the "{skill_name}" skill, indicating they want you to follow its instructions. The full skill content is loaded below.]',
                "",
                content,
            ]
        )

    if skipped:
        notice = (
            f"[SYSTEM: The following skill(s) were listed for this job but could not be found "
            f"and were skipped: {', '.join(skipped)}. "
            f"Start your response with a brief notice so the user is aware, e.g.: "
            f"'⚠️ Skill(s) not found and skipped: {', '.join(skipped)}']"
        )
        parts.insert(0, notice)

    if prompt:
        parts.extend(["", f"The user has provided the following instruction alongside the skill invocation: {prompt}"])
    return "\n".join(parts)


def run_job(job: dict) -> tuple[bool, str, str, Optional[str]]:
    """
    Execute a single cron job.
    
    Returns:
        Tuple of (success, full_output_doc, final_response, error_message)
    """
    # Initialize SQLite session store so cron job messages are persisted
    # and discoverable via session_search (same pattern as gateway/run.py).
    _session_db = None
    try:
        from hermes_state import SessionDB
        _session_db = SessionDB()
    except Exception as e:
        logger.debug("Job '%s': SQLite session store not available: %s", job.get("id", "?"), e)
    
    job_id = job["id"]
    job_name = job["name"]
    prompt = _build_job_prompt(job)
    origin = _resolve_origin(job)
    _cron_session_id = f"cron_{job_id}_{_hermes_now().strftime('%Y%m%d_%H%M%S')}"

    logger.info("Running job '%s' (ID: %s)", job_name, job_id)
    logger.info("Prompt: %s", prompt[:100])

    try:
        # Inject origin context so the agent's send_message tool knows the chat.
        # Must be INSIDE the try block so the finally cleanup always runs.
        if origin:
            os.environ["HERMES_SESSION_PLATFORM"] = origin["platform"]
            os.environ["HERMES_SESSION_CHAT_ID"] = str(origin["chat_id"])
            if origin.get("chat_name"):
                os.environ["HERMES_SESSION_CHAT_NAME"] = origin["chat_name"]
        # Re-read .env and config.yaml fresh every run so provider/key
        # changes take effect without a gateway restart.
        from dotenv import load_dotenv
        try:
            load_dotenv(str(_hermes_home / ".env"), override=True, encoding="utf-8")
        except UnicodeDecodeError:
            load_dotenv(str(_hermes_home / ".env"), override=True, encoding="latin-1")

        delivery_target = _resolve_delivery_target(job)
        if delivery_target:
            os.environ["HERMES_CRON_AUTO_DELIVER_PLATFORM"] = delivery_target["platform"]
            os.environ["HERMES_CRON_AUTO_DELIVER_CHAT_ID"] = str(delivery_target["chat_id"])
            if delivery_target.get("thread_id") is not None:
                os.environ["HERMES_CRON_AUTO_DELIVER_THREAD_ID"] = str(delivery_target["thread_id"])

        direct_response = _run_direct_cron_tool(job)
        if direct_response is not None:
            output = _format_job_success_output(job, prompt, direct_response)
            logger.info("Job '%s' completed successfully via direct tool", job_name)
            return True, output, direct_response, None

        model = job.get("model") or os.getenv("HERMES_MODEL") or ""

        # Load config.yaml for model, reasoning, prefill, toolsets, provider routing
        _cfg = {}
        try:
            import yaml
            _cfg_path = str(_hermes_home / "config.yaml")
            if os.path.exists(_cfg_path):
                with open(_cfg_path) as _f:
                    _cfg = yaml.safe_load(_f) or {}
                _model_cfg = _cfg.get("model", {})
                if not job.get("model"):
                    if isinstance(_model_cfg, str):
                        model = _model_cfg
                    elif isinstance(_model_cfg, dict):
                        model = _model_cfg.get("default", model)
        except Exception as e:
            logger.warning("Job '%s': failed to load config.yaml, using defaults: %s", job_id, e)

        # Reasoning config from config.yaml
        from hermes_constants import parse_reasoning_effort
        effort = str(_cfg.get("agent", {}).get("reasoning_effort", "")).strip()
        reasoning_config = parse_reasoning_effort(effort)

        # Prefill messages from env or config.yaml
        prefill_messages = None
        prefill_file = os.getenv("HERMES_PREFILL_MESSAGES_FILE", "") or _cfg.get("prefill_messages_file", "")
        if prefill_file:
            import json as _json
            pfpath = Path(prefill_file).expanduser()
            if not pfpath.is_absolute():
                pfpath = _hermes_home / pfpath
            if pfpath.exists():
                try:
                    with open(pfpath, "r", encoding="utf-8") as _pf:
                        prefill_messages = _json.load(_pf)
                    if not isinstance(prefill_messages, list):
                        prefill_messages = None
                except Exception as e:
                    logger.warning("Job '%s': failed to parse prefill messages file '%s': %s", job_id, pfpath, e)
                    prefill_messages = None

        # Max iterations
        max_iterations = _cfg.get("agent", {}).get("max_turns") or _cfg.get("max_turns") or 90

        # Provider routing
        pr = _cfg.get("provider_routing", {})
        smart_routing = _cfg.get("smart_model_routing", {}) or {}

        from hermes_cli.runtime_provider import (
            resolve_runtime_provider,
            format_runtime_provider_error,
        )
        try:
            runtime_kwargs = {
                "requested": job.get("provider") or os.getenv("HERMES_INFERENCE_PROVIDER"),
            }
            if job.get("base_url"):
                runtime_kwargs["explicit_base_url"] = job.get("base_url")
            runtime = resolve_runtime_provider(**runtime_kwargs)
        except Exception as exc:
            message = format_runtime_provider_error(exc)
            raise RuntimeError(message) from exc

        from agent.smart_model_routing import resolve_turn_route
        turn_route = resolve_turn_route(
            prompt,
            smart_routing,
            {
                "model": model,
                "api_key": runtime.get("api_key"),
                "base_url": runtime.get("base_url"),
                "provider": runtime.get("provider"),
                "api_mode": runtime.get("api_mode"),
                "command": runtime.get("command"),
                "args": list(runtime.get("args") or []),
            },
        )

        from run_agent import AIAgent
        agent = AIAgent(
            model=turn_route["model"],
            api_key=turn_route["runtime"].get("api_key"),
            base_url=turn_route["runtime"].get("base_url"),
            provider=turn_route["runtime"].get("provider"),
            api_mode=turn_route["runtime"].get("api_mode"),
            acp_command=turn_route["runtime"].get("command"),
            acp_args=turn_route["runtime"].get("args"),
            max_iterations=max_iterations,
            reasoning_config=reasoning_config,
            prefill_messages=prefill_messages,
            providers_allowed=pr.get("only"),
            providers_ignored=pr.get("ignore"),
            providers_order=pr.get("order"),
            provider_sort=pr.get("sort"),
            disabled_toolsets=["cronjob", "messaging", "clarify"],
            quiet_mode=True,
            skip_memory=True,  # Cron system prompts would corrupt user representations
            platform="cron",
            session_id=_cron_session_id,
            session_db=_session_db,
        )
        
        # Run the agent with an *inactivity*-based timeout: the job can run
        # for hours if it's actively calling tools / receiving stream tokens,
        # but a hung API call or stuck tool with no activity for the configured
        # duration is caught and killed.  Default 600s (10 min inactivity);
        # override via job.timeout_seconds or HERMES_CRON_TIMEOUT env var.
        # 0 = unlimited.
        #
        # Uses the agent's built-in activity tracker (updated by
        # _touch_activity() on every tool call, API call, and stream delta).
        _cron_timeout = float(job.get("timeout_seconds") or os.getenv("HERMES_CRON_TIMEOUT", 600))
        _cron_inactivity_limit = _cron_timeout if _cron_timeout > 0 else None
        _POLL_INTERVAL = 5.0
        _cron_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        _cron_future = _cron_pool.submit(agent.run_conversation, prompt)
        _inactivity_timeout = False
        try:
            if _cron_inactivity_limit is None:
                # Unlimited — just wait for the result.
                result = _cron_future.result()
            else:
                result = None
                while True:
                    done, _ = concurrent.futures.wait(
                        {_cron_future}, timeout=_POLL_INTERVAL,
                    )
                    if done:
                        result = _cron_future.result()
                        break
                    # Agent still running — check inactivity.
                    _idle_secs = 0.0
                    if hasattr(agent, "get_activity_summary"):
                        try:
                            _act = agent.get_activity_summary()
                            _idle_secs = _act.get("seconds_since_activity", 0.0)
                        except Exception:
                            pass
                    if _idle_secs >= _cron_inactivity_limit:
                        _inactivity_timeout = True
                        break
        except Exception:
            _cron_pool.shutdown(wait=False, cancel_futures=True)
            raise
        finally:
            _cron_pool.shutdown(wait=False)

        if _inactivity_timeout:
            # Build diagnostic summary from the agent's activity tracker.
            _activity = {}
            if hasattr(agent, "get_activity_summary"):
                try:
                    _activity = agent.get_activity_summary()
                except Exception:
                    pass
            _last_desc = _activity.get("last_activity_desc", "unknown")
            _secs_ago = _activity.get("seconds_since_activity", 0)
            _cur_tool = _activity.get("current_tool")
            _iter_n = _activity.get("api_call_count", 0)
            _iter_max = _activity.get("max_iterations", 0)

            logger.error(
                "Job '%s' idle for %.0fs (inactivity limit %.0fs) "
                "| last_activity=%s | iteration=%s/%s | tool=%s",
                job_name, _secs_ago, _cron_inactivity_limit,
                _last_desc, _iter_n, _iter_max,
                _cur_tool or "none",
            )
            if hasattr(agent, "interrupt"):
                agent.interrupt("Cron job timed out (inactivity)")
            raise TimeoutError(
                f"Cron job '{job_name}' idle for "
                f"{int(_secs_ago)}s (limit {int(_cron_inactivity_limit)}s) "
                f"— last activity: {_last_desc}"
            )

        final_response = result.get("final_response", "") or ""
        output = _format_job_success_output(job, prompt, final_response)
        
        logger.info("Job '%s' completed successfully", job_name)
        return True, output, final_response, None
        
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        logger.exception("Job '%s' failed: %s", job_name, error_msg)
        
        output = f"""# Cron Job: {job_name} (FAILED)

**Job ID:** {job_id}
**Run Time:** {_hermes_now().strftime('%Y-%m-%d %H:%M:%S')}
**Schedule:** {job.get('schedule_display', 'N/A')}

## Prompt

{prompt}

## Error

```
{error_msg}
```
"""
        return False, output, "", error_msg

    finally:
        # Clean up injected env vars so they don't leak to other jobs
        for key in (
            "HERMES_SESSION_PLATFORM",
            "HERMES_SESSION_CHAT_ID",
            "HERMES_SESSION_CHAT_NAME",
            "HERMES_CRON_AUTO_DELIVER_PLATFORM",
            "HERMES_CRON_AUTO_DELIVER_CHAT_ID",
            "HERMES_CRON_AUTO_DELIVER_THREAD_ID",
        ):
            os.environ.pop(key, None)
        if _session_db:
            try:
                _session_db.end_session(_cron_session_id, "cron_complete")
            except (Exception, KeyboardInterrupt) as e:
                logger.debug("Job '%s': failed to end session: %s", job_id, e)
            try:
                _session_db.close()
            except (Exception, KeyboardInterrupt) as e:
                logger.debug("Job '%s': failed to close SQLite session store: %s", job_id, e)


def _acquire_scheduler_lock() -> Optional[object]:
    """Acquire the scheduler's non-blocking process lock, or return ``None``."""
    _LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = None
    try:
        lock_fd = open(_LOCK_FILE, "w")
        if fcntl:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
        return lock_fd
    except (OSError, IOError):
        if lock_fd is not None:
            lock_fd.close()
        return None


def _release_scheduler_lock(lock_fd) -> None:
    if fcntl:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    elif msvcrt:
        try:
            msvcrt.locking(lock_fd.fileno(), msvcrt.LK_UNLCK, 1)
        except (OSError, IOError):
            pass
    lock_fd.close()


def _run_selected_job_unlocked(job_id: str, verbose: bool = True, adapters=None, loop=None) -> int:
    """Run only the named job. The caller must hold the scheduler lock."""
    job = get_job(job_id)
    if job is None:
        return 0
    try:
        advance_next_run(job["id"])
        success, output, final_response, error = run_job(job)
        output_file = save_job_output(job["id"], output)
        if verbose:
            logger.info("Output saved to: %s", output_file)

        deliver_content = final_response if success else f"⚠️ Cron job '{job.get('name', job['id'])}' failed:\n{error}"
        suppress_daily_goal_delivery = bool(
            job.get("_daily_goal_dry_run")
            or job.get("_daily_goal_suppress_delivery")
        )
        should_deliver = bool(deliver_content) and not suppress_daily_goal_delivery
        if should_deliver and success and SILENT_MARKER in deliver_content.strip().upper():
            logger.info("Job '%s': agent returned %s — skipping delivery", job["id"], SILENT_MARKER)
            should_deliver = False

        delivery_error = None
        delivery_exception = None
        daily_goal_telegram_attempt = False
        if should_deliver:
            if job.get("_daily_goal_cycle_id"):
                try:
                    delivery_target = _resolve_delivery_target(job)
                except Exception as target_error:
                    delivery_target = None
                    logger.error("Job '%s': daily goal delivery target resolution failed: %s", job["id"], target_error)
                daily_goal_telegram_attempt = bool(
                    delivery_target and str(delivery_target.get("platform", "")).lower() == "telegram"
                )
                if daily_goal_telegram_attempt:
                    try:
                        from tools.daily_goal_coordinator_tool import (
                            begin_daily_goal_delivery,
                        )

                        if not begin_daily_goal_delivery(
                            job["_daily_goal_cycle_id"],
                            job.get("_daily_goal_delivery_kind", "original"),
                        ):
                            should_deliver = False
                            logger.warning(
                                "Job '%s': daily goal outbox is not eligible for delivery",
                                job["id"],
                            )
                    except Exception as begin_error:
                        should_deliver = False
                        logger.error(
                            "Job '%s': daily goal delivery could not begin: %s",
                            job["id"],
                            begin_error,
                        )
            if not should_deliver:
                daily_goal_telegram_attempt = False
            else:
                try:
                    delivery_error = _deliver_result(job, deliver_content, adapters=adapters, loop=loop)
                except Exception as de:
                    delivery_exception = de
                    delivery_error = str(de) or type(de).__name__
                    logger.error("Delivery failed for job %s: %s", job["id"], de)

        cycle_id = job.get("_daily_goal_cycle_id")
        if cycle_id and should_deliver and daily_goal_telegram_attempt:
            try:
                from tools.daily_goal_coordinator_tool import record_daily_goal_delivery

                if delivery_exception is not None:
                    delivery_status = "unknown" if isinstance(delivery_exception, (TimeoutError, ConnectionError)) else "failed"
                elif delivery_error:
                    delivery_status = (
                        "failed"
                        if getattr(delivery_error, "definitive", False)
                        else "unknown"
                    )
                else:
                    delivery_status = "delivered"
                record_daily_goal_delivery(
                    cycle_id,
                    delivery_status,
                    job.get("_daily_goal_delivery_kind", "original"),
                    str(delivery_error or delivery_exception or ""),
                )
            except Exception as re:
                logger.error("Job '%s': daily goal delivery reconciliation failed: %s", job["id"], re)

        mark_job_run(job["id"], success, error, delivery_error=delivery_error)
        return 1
    except Exception as e:
        logger.error("Error processing job %s: %s", job_id, e)
        mark_job_run(job_id, False, str(e))
        return 1


def run_selected_job(job_id: str, verbose: bool = True, adapters=None, loop=None) -> int:
    """Run one selected job while holding the same lock used by ``tick``."""
    lock_fd = _acquire_scheduler_lock()
    if lock_fd is None:
        logger.debug("Selected job '%s' skipped — scheduler lock is held", job_id)
        return 0
    try:
        return _run_selected_job_unlocked(job_id, verbose=verbose, adapters=adapters, loop=loop)
    finally:
        _release_scheduler_lock(lock_fd)


def trigger_and_run_selected_job(
    job_id: str,
    *,
    verbose: bool = True,
    adapters=None,
    loop=None,
) -> str:
    """Atomically trigger and run one job, reporting busy/executed/failed."""
    lock_fd = _acquire_scheduler_lock()
    if lock_fd is None:
        logger.warning(
            "Selected job '%s' was not triggered because the scheduler is busy",
            job_id,
        )
        return "busy"
    try:
        if trigger_job(job_id) is None:
            return "failed"
        result = _run_selected_job_unlocked(
            job_id,
            verbose=verbose,
            adapters=adapters,
            loop=loop,
        )
        current = get_job(job_id)
        if (
            result == 1
            and isinstance(current, dict)
            and current.get("last_status") == "ok"
        ):
            return "executed"
        return "failed"
    except Exception as exc:
        logger.error("Selected job '%s' trigger/run failed: %s", job_id, exc)
        return "failed"
    finally:
        _release_scheduler_lock(lock_fd)


def tick(verbose: bool = True, adapters=None, loop=None) -> int:
    """
    Check and run all due jobs.
    
    Uses a file lock so only one tick runs at a time, even if the gateway's
    in-process ticker and a standalone daemon or manual tick overlap.
    
    Args:
        verbose: Whether to print status messages
        adapters: Optional dict mapping Platform → live adapter (from gateway)
        loop: Optional asyncio event loop (from gateway) for live adapter sends
    
    Returns:
        Number of jobs executed (0 if another tick is already running)
    """
    lock_fd = _acquire_scheduler_lock()
    if lock_fd is None:
        logger.debug("Tick skipped — another instance holds the lock")
        return 0

    try:
        due_jobs = get_due_jobs()

        if verbose and not due_jobs:
            logger.info("%s - No jobs due", _hermes_now().strftime('%H:%M:%S'))
            return 0

        if verbose:
            logger.info("%s - %s job(s) due", _hermes_now().strftime('%H:%M:%S'), len(due_jobs))

        executed = 0
        for job in due_jobs:
            try:
                # For recurring jobs (cron/interval), advance next_run_at to the
                # next future occurrence BEFORE execution.  This way, if the
                # process crashes mid-run, the job won't re-fire on restart.
                # One-shot jobs are left alone so they can retry on restart.
                advance_next_run(job["id"])

                success, output, final_response, error = run_job(job)

                output_file = save_job_output(job["id"], output)
                if verbose:
                    logger.info("Output saved to: %s", output_file)

                # Deliver the final response to the origin/target chat.
                # If the agent responded with [SILENT], skip delivery (but
                # output is already saved above).  Failed jobs always deliver.
                deliver_content = final_response if success else f"⚠️ Cron job '{job.get('name', job['id'])}' failed:\n{error}"
                suppress_daily_goal_delivery = bool(
                    job.get("_daily_goal_dry_run")
                    or job.get("_daily_goal_suppress_delivery")
                )
                should_deliver = bool(deliver_content) and not suppress_daily_goal_delivery
                if should_deliver and success and SILENT_MARKER in deliver_content.strip().upper():
                    logger.info("Job '%s': agent returned %s — skipping delivery", job["id"], SILENT_MARKER)
                    should_deliver = False

                delivery_error = None
                delivery_exception = None
                daily_goal_telegram_attempt = False
                if should_deliver:
                    if job.get("_daily_goal_cycle_id"):
                        try:
                            delivery_target = _resolve_delivery_target(job)
                        except Exception as target_error:
                            delivery_target = None
                            logger.error(
                                "Job '%s': daily goal delivery target resolution failed: %s",
                                job["id"],
                                target_error,
                            )
                        daily_goal_telegram_attempt = bool(
                            delivery_target
                            and str(delivery_target.get("platform", "")).lower()
                            == "telegram"
                        )
                        if daily_goal_telegram_attempt:
                            try:
                                from tools.daily_goal_coordinator_tool import (
                                    begin_daily_goal_delivery,
                                )

                                if not begin_daily_goal_delivery(
                                    job["_daily_goal_cycle_id"],
                                    job.get("_daily_goal_delivery_kind", "original"),
                                ):
                                    should_deliver = False
                                    logger.warning(
                                        "Job '%s': daily goal outbox is not eligible for delivery",
                                        job["id"],
                                    )
                            except Exception as begin_error:
                                should_deliver = False
                                logger.error(
                                    "Job '%s': daily goal delivery could not begin: %s",
                                    job["id"],
                                    begin_error,
                                )
                    if not should_deliver:
                        daily_goal_telegram_attempt = False
                    else:
                        try:
                            delivery_error = _deliver_result(job, deliver_content, adapters=adapters, loop=loop)
                        except Exception as de:
                            delivery_exception = de
                            delivery_error = str(de) or type(de).__name__
                            logger.error("Delivery failed for job %s: %s", job["id"], de)

                cycle_id = job.get("_daily_goal_cycle_id")
                if cycle_id and should_deliver and daily_goal_telegram_attempt:
                    try:
                        from tools.daily_goal_coordinator_tool import (
                            record_daily_goal_delivery,
                        )

                        if delivery_exception is not None:
                            delivery_status = (
                                "unknown"
                                if isinstance(
                                    delivery_exception,
                                    (TimeoutError, ConnectionError),
                                )
                                else "failed"
                            )
                        elif delivery_error:
                            delivery_status = (
                                "failed"
                                if getattr(delivery_error, "definitive", False)
                                else "unknown"
                            )
                        else:
                            delivery_status = "delivered"
                        record_daily_goal_delivery(
                            cycle_id,
                            delivery_status,
                            job.get("_daily_goal_delivery_kind", "original"),
                            str(delivery_error or delivery_exception or ""),
                        )
                    except Exception as re:
                        logger.error(
                            "Job '%s': daily goal delivery reconciliation failed: %s",
                            job["id"],
                            re,
                        )

                mark_job_run(job["id"], success, error, delivery_error=delivery_error)
                executed += 1

            except Exception as e:
                logger.error("Error processing job %s: %s", job['id'], e)
                mark_job_run(job["id"], False, str(e))

        return executed
    finally:
        _release_scheduler_lock(lock_fd)


if __name__ == "__main__":
    tick(verbose=True)
