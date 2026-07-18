"""harness-guard plugin — Post-execution result-correctness guard.

Two hooks:
1. post_tool_call: Record audit log entry (every tool call, zero latency).
2. transform_tool_result: For write operations, send tool result + audit trail
   to a review model for correctness check. If review fails, replace the tool
   result with a feedback message so the model knows why and how to fix it.

Configuration:
  The plugin reads its own .env file located in this directory
  (~/.hermes/plugins/harness-guard/.env). Copy `.env.example` to `.env`
  and fill in your values. System-level env vars of the same name still
  take precedence (so you can override per-machine without editing the file).

Environment variables:
  HARNESS_GUARD_API_KEY             API key (priority: plugin .env, then env)
  HARNESS_GUARD_BASE_URL            OpenAI-compatible chat completions base URL
  HARNESS_GUARD_MODEL               Model name (e.g. glm-5.2, gemini-3.5-flash)
  HARNESS_GUARD_TIMEOUT_S           Request timeout in seconds (default 60)
  HARNESS_GUARD_MAX_AUDIT_TRAIL_CHARS  Max chars of audit trail (default 4000)
  HARNESS_GUARD_DISABLE=1           Disable the plugin entirely (fail-open).
  ZAI_API_KEY / GLM_API_KEY         Backward-compat key aliases.
"""

from __future__ import annotations

import json
import logging
import os
import time as _time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Load the plugin's own .env (if present) BEFORE importing submodules that
# may read env vars at import time. System env wins on conflict (override=True).
_PLUGIN_DIR = Path(__file__).resolve().parent
_PLUGIN_ENV = _PLUGIN_DIR / ".env"


def _load_plugin_dotenv() -> int:
    """Load KEY=VALUE lines from the plugin's own .env into os.environ.

    System-level env vars take precedence (we don't clobber them). Returns
    the number of variables set from the file.
    """
    if not _PLUGIN_ENV.is_file():
        return 0
    loaded = 0
    try:
        with _PLUGIN_ENV.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if not key:
                    continue
                if key in os.environ:
                    # System / global env wins — plugin .env is the fallback
                    continue
                os.environ[key] = value
                loaded += 1
    except OSError as exc:
        logger.warning("harness-guard: failed to read %s: %s", _PLUGIN_ENV, exc)
    return loaded


# Submodule imports — all symbols used in hook callbacks are imported here
# at package level so callbacks can resolve them without per-call import.
# Note: _load_plugin_dotenv() is invoked at L85 (after this block); submodules
# read env vars lazily via os.getenv() at call time, so import order relative
# to _load_plugin_dotenv() does not affect env visibility.
from .audit_log import AuditEntry, get_log, summarize_args, summarize_result
from .project_config import (
    load_project_config,
    get_merged_terminal_patterns,
    get_merged_protected_paths,
    get_merged_custom_rules,
)
from .reviewer import review
from .rules import should_review

logger = logging.getLogger(__name__)
_PLUG_ENV_VARS_LOADED = _load_plugin_dotenv()
if _PLUG_ENV_VARS_LOADED:
    logger.info(
        "harness-guard: loaded %d env var(s) from %s",
        _PLUG_ENV_VARS_LOADED,
        _PLUGIN_ENV,
    )


def _is_disabled() -> bool:
    """Check if the plugin is disabled via config or env var."""
    if os.environ.get("HARNESS_GUARD_DISABLE", "").lower() in {"1", "true", "yes", "on"}:
        return True
    return False


def _on_post_tool_call(
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    duration_ms: int = 0,
    **_kwargs: Any,
) -> None:
    """Record every tool call in the audit log."""
    if _is_disabled():
        return
    entry = AuditEntry(
        tool=tool_name,
        args_summary=summarize_args(args),
        result_summary=summarize_result(result),
        ts=_time.time(),
    )
    effective_session = session_id or task_id or "unknown"
    get_log().append(effective_session, entry)


def _on_transform_tool_result(
    tool_name: str = "",
    args: Any = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    **_kwargs: Any,
) -> Optional[str]:
    """Review write operations via GLM-5.2.

    Returns a string to replace the tool result if review fails.
    Returns None to leave the result unchanged.
    """
    if _is_disabled():
        return None

    # Load per-project config (empty dict if no .hermes-guard.yaml found)
    proj_cfg = load_project_config()
    terminal_patterns = get_merged_terminal_patterns(proj_cfg)
    protected_paths = get_merged_protected_paths(proj_cfg)
    custom_rules = get_merged_custom_rules(proj_cfg)

    # Only review specific tools
    args_dict = args if isinstance(args, dict) else {}
    if not should_review(tool_name, args_dict, terminal_patterns=terminal_patterns):
        return None

    # Don't review error results — the model already has problems
    if isinstance(result, str):
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict) and "error" in parsed and len(parsed) <= 2:
                return None
        except (ValueError, TypeError):
            pass

    effective_session = session_id or task_id or "unknown"
    log = get_log()
    recent = log.get_recent(effective_session)

    # Build audit trail string
    trail_lines: List[str] = []
    for entry in recent:
        trail_lines.append(
            f"[{entry.tool}] args: {entry.args_summary} -> result: {entry.result_summary}"
        )
    audit_trail = "\n".join(trail_lines)

    # Build args string for reviewer
    args_str = summarize_args(args, max_len=2000)
    result_str = summarize_result(result, max_len=2000)

    # Call GLM-5.2 reviewer
    feedback = review(
        tool_name=tool_name,
        args_str=args_str,
        result_str=result_str,
        audit_trail_str=audit_trail,
        protected_paths=protected_paths,
        custom_rules=custom_rules,
    )

    if feedback:
        logger.info(
            "harness-guard: BLOCKED %s (session=%s)",
            tool_name,
            effective_session[:12],
        )
        # Wrap the feedback as a JSON error so the model sees it as a tool error
        # but with clear guidance on how to fix it
        return json.dumps(
            {"harness_guard_review": True, "message": feedback},
            ensure_ascii=False,
        )

    return None


def register(ctx) -> None:
    """Register hooks with Hermes."""
    logger.info("harness-guard: registering post_tool_call + transform_tool_result hooks")
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
    ctx.register_hook("on_session_end", _on_session_end)

    # Register CLI subcommands
    from .cli import register_guard_cli
    register_guard_cli(ctx)


def _on_session_end(
    session_id: str = "",
    **_kwargs: Any,
) -> None:
    """Clean up audit log when session ends."""
    if session_id:
        get_log().clear_session(session_id)
