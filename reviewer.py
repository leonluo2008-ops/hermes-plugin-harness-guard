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

_DEFAULT_PROVIDER = "glm"

# Pre-defined configurations for common providers
_PROVIDER_PRESETS = {
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
        "model": "glm-5.2",
    },
    "minimax": {
        "base_url": "https://api.minimaxi.com/v1",
        "model": "MiniMax-M3",
    },
    "juxin": {
        "base_url": "https://api.jxincm.cn/v1",
        "model": "gemini-3.5-flash",
    }
}

_DEFAULT_TIMEOUT_S = 60
_DEFAULT_MAX_AUDIT_TRAIL_CHARS = 4000


def _get_provider_config() -> dict:
    """Get the active provider preset configuration.

    Resolution:
    1. Check HARNESS_GUARD_PROVIDER (e.g., 'glm', 'minimax', 'juxin')
    2. Fallback to default 'glm'
    """
    provider = os.getenv("HARNESS_GUARD_PROVIDER", _DEFAULT_PROVIDER).strip().lower()
    return _PROVIDER_PRESETS.get(provider, _PROVIDER_PRESETS[_DEFAULT_PROVIDER])


def _get_api_base() -> str:
    """API base URL.

    Override priority:
    1. HARNESS_GUARD_BASE_URL env var
    2. Resolved provider's default base_url
    """
    env_override = os.getenv("HARNESS_GUARD_BASE_URL", "").strip()
    if env_override:
        return env_override.rstrip("/")
    return _get_provider_config()["base_url"].rstrip("/")


def _get_model() -> str:
    """Model name to use for review.

    Override priority:
    1. HARNESS_GUARD_MODEL env var
    2. Resolved provider's default model
    """
    env_override = os.getenv("HARNESS_GUARD_MODEL", "").strip()
    if env_override:
        return env_override
    return _get_provider_config()["model"]


def _get_api_key() -> str:
    """API key for the review endpoint.

    Resolution order:
    1. HARNESS_GUARD_API_KEY  (plugin-specific, recommended)
    2. ZAI_API_KEY            (legacy GLM-5.2 default, backward compatible)
    3. GLM_API_KEY            (alternative env name used by some zai setups)
    4. MINIMAX_CN_API_KEY     (alternative env name for minimax setup)
    5. JUXIN_GEMINI_API_KEY   (alternative env name for juxin setup)

    Returns empty string if none set — caller should fail-open.
    """
    return (
        os.getenv("HARNESS_GUARD_API_KEY", "")
        or os.getenv("ZAI_API_KEY", "")
        or os.getenv("GLM_API_KEY", "")
        or os.getenv("MINIMAX_CN_API_KEY", "")
        or os.getenv("JUXIN_GEMINI_API_KEY", "")
    ).strip()


def _get_timeout_s() -> int:
    """Review timeout in seconds (default 60).

    Override via HARNESS_GUARD_TIMEOUT_S env var (integer seconds).
    """
    try:
        return int(os.getenv("HARNESS_GUARD_TIMEOUT_S", str(_DEFAULT_TIMEOUT_S)).strip())
    except (ValueError, TypeError):
        return _DEFAULT_TIMEOUT_S


def _get_max_audit_trail_chars() -> int:
    """Max audit trail chars passed to review prompt (default 4000).

    Override via HARNESS_GUARD_MAX_AUDIT_TRAIL_CHARS env var (integer).
    """
    try:
        return int(os.getenv("HARNESS_GUARD_MAX_AUDIT_TRAIL_CHARS", str(_DEFAULT_MAX_AUDIT_TRAIL_CHARS)).strip())
    except (ValueError, TypeError):
        return _DEFAULT_MAX_AUDIT_TRAIL_CHARS


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
        logger.warning(
            "harness-guard: no API key set (HARNESS_GUARD_API_KEY / ZAI_API_KEY / "
            "GLM_API_KEY), skipping review"
        )
        return None

    api_base = _get_api_base()
    model = _get_model()
    timeout_s = _get_timeout_s()
    max_audit_chars = _get_max_audit_trail_chars()

    # Truncate audit trail if too long — keep head (earliest reads are key evidence)
    if len(audit_trail_str) > max_audit_chars:
        audit_trail_str = audit_trail_str[:max_audit_chars] + "\n... (truncated)"

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
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
        "temperature": 0.1,
    }

    try:
        import httpx

        start = time.monotonic()
        resp = httpx.post(
            f"{api_base}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout_s,
        )
        elapsed = time.monotonic() - start
        logger.info("harness-guard: review took %.1fs for %s", elapsed, tool_name)

        if resp.status_code != 200:
            logger.warning("harness-guard: API returned %d", resp.status_code)
            return None

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        # Strip <think>...</think> reasoning tags emitted by thinking-capable
        # models (e.g. GLM-5.2, MiniMax-M3, DeepSeek R1) so the PASS/FAIL
        # detector only sees the actual verdict.
        content = re.sub(r"<think>.*?</think>\s*", "", content, flags=re.DOTALL).strip()

        if not content:
            return None

        # Parse response
        logger.info("harness-guard: review response from %s: %s", model, content[:500])
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
