"""GLM-5.2 reviewer for harness-guard plugin.

Synchronous HTTP call to GLM-5.2 API. Called from transform_tool_result hook
which is itself synchronous (invoke_hook calls cb(**kwargs)).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time

logger = logging.getLogger(__name__)

_API_BASE = "https://open.bigmodel.cn/api/coding/paas/v4"
_MODEL = "glm-5.2"
_TIMEOUT_S = 60
_MAX_AUDIT_TRAIL_CHARS = 4000


def _get_api_key() -> str:
    return os.getenv("ZAI_API_KEY", "").strip()


def review(
    tool_name: str,
    args_str: str,
    result_str: str,
    audit_trail_str: str,
    protected_paths: tuple[str, ...] | None = None,
    custom_rules: list[str] | None = None,
) -> str | None:
    """Call GLM-5.2 to review a tool call result.

    Returns:
        None if review passes (or on error/fail-open).
        A feedback string to replace the tool result if review fails.
    """
    # Fail-open: if no API key, skip review silently
    api_key = _get_api_key()
    if not api_key:
        logger.warning("harness-guard: ZAI_API_KEY not found, skipping review")
        return None

    # Truncate audit trail if too long — keep head (earliest reads are key evidence)
    if len(audit_trail_str) > _MAX_AUDIT_TRAIL_CHARS:
        audit_trail_str = audit_trail_str[:_MAX_AUDIT_TRAIL_CHARS] + "\n... (truncated)"

    # Build prompt
    from .rules import build_review_prompt

    prompt = build_review_prompt(
        audit_trail=audit_trail_str,
        tool_name=tool_name,
        args=args_str,
        result=result_str,
        protected_paths=protected_paths,
        custom_rules=custom_rules,
    )

    payload = {
        "model": _MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
        "temperature": 0.1,
    }

    try:
        import httpx

        start = time.monotonic()
        resp = httpx.post(
            f"{_API_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=_TIMEOUT_S,
        )
        elapsed = time.monotonic() - start
        logger.info("harness-guard: review took %.1fs for %s", elapsed, tool_name)

        if resp.status_code != 200:
            logger.warning("harness-guard: API returned %d", resp.status_code)
            return None

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        if not content:
            return None

        # Parse response
        logger.info("harness-guard: GLM response: %s", content[:500])
        if content.upper().startswith("PASS"):
            return None  # Review passed, don't modify result

        # Extract FAIL reason and fix (handle multi-line)
        reason_match = re.search(r"Reason:\s*(.*?)\n\s*Fix:", content, re.DOTALL | re.IGNORECASE)
        fix_match = re.search(r"Fix:\s*(.+)", content, re.IGNORECASE)
        reason = reason_match.group(1).strip() if reason_match else "Result correctness review failed."
        fix = fix_match.group(1).strip() if fix_match else "Please verify the output and try again."
        return (
            "⚠️ harness-guard 审查不通过\n\n"
            f"原因：{reason}\n"
            f"建议：{fix}\n\n"
            "请根据上述建议修正后重试。如果你确定当前操作是正确的，请继续。"
        )

        # Unrecognized response format — fail-open
        logger.warning("harness-guard: unrecognized review response: %s", content[:200])
        return None

    except Exception as exc:
        logger.warning("harness-guard: review error: %s", exc)
        return None  # Fail-open: don't break the agent
