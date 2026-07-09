"""harness-guard plugin — Post-execution result-correctness guard.

Two hooks:
1. post_tool_call: Record audit log entry (every tool call, zero latency).
2. transform_tool_result: For write operations, send tool result + audit trail
   to GLM-5.2 for correctness review. If review fails, replace the tool result
   with a feedback message so the model knows why and how to fix it.

Environment variables:
  HARNESS_GUARD_DISABLE=1   Disable the plugin entirely (fail-open).
"""

from __future__ import annotations

import json
import logging
import os
import time as _time
from typing import Any, Dict, List, Optional

from .audit_log import AuditEntry, AuditLog, get_log, summarize_args, summarize_result
from .reviewer import review
from .rules import should_review

logger = logging.getLogger(__name__)


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

    # Only review specific tools
    args_dict = args if isinstance(args, dict) else {}
    if not should_review(tool_name, args_dict):
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


def _on_session_end(
    session_id: str = "",
    **_kwargs: Any,
) -> None:
    """Clean up audit log when session ends."""
    if session_id:
        get_log().clear_session(session_id)
