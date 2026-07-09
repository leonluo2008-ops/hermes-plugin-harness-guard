"""Review rules for harness-guard plugin.

Defines which tools trigger review and the prompt template for GLM-5.2.
"""

from __future__ import annotations

import re

# Tools that always trigger review (write operations where result correctness matters)
REVIEW_TOOLS = frozenset({
    "write_file",
    "patch",
    "skill_manage",
})

# Patterns in terminal command that trigger review
TERMINAL_REVIEW_PATTERNS = [
    re.compile(r"hermes\s+config\s+(set|delete|edit)", re.IGNORECASE),
    re.compile(r"rm\s+-rf", re.IGNORECASE),
    re.compile(r"rm\s+-r\s", re.IGNORECASE),
    re.compile(r"git\s+push.*(--force|-f)", re.IGNORECASE),
    re.compile(r"git\s+reset\s+--hard", re.IGNORECASE),
    re.compile(r"systemctl\s+(stop|disable|restart)", re.IGNORECASE),
    re.compile(r"docker\s+(rm|rmi|stop|kill)", re.IGNORECASE),
    re.compile(r"crontab", re.IGNORECASE),
]

# Paths that are user-protected (require explicit user authorization in audit log)
PROTECTED_PATHS = (
    "SOUL.md",
    ".hermes.md",
    "config.yaml",
    "jobs.json",
)


def should_review(tool_name: str, args: dict) -> bool:
    """Check if this tool call should trigger GLM-5.2 review."""
    if tool_name in REVIEW_TOOLS:
        # For skill_manage, only review write/patch modes
        if tool_name == "skill_manage":
            action = args.get("action", "")
            if action not in ("create", "edit", "patch", "write_file", "remove_file"):
                return False
        return True

    if tool_name == "terminal":
        command = args.get("command", "")
        for pattern in TERMINAL_REVIEW_PATTERNS:
            if pattern.search(command):
                return True

    return False


def build_review_prompt(audit_trail: str, tool_name: str, args: str, result: str) -> str:
    """Build the review prompt with dynamically inserted protected files list."""
    protected_list = "\n".join(f"- {p}" for p in PROTECTED_PATHS)
    return f"""You are a result-correctness reviewer for an AI agent. The agent just executed a tool call and you must verify whether the result is correct.

## Audit Trail (recent tool calls in this session, oldest first)
{audit_trail}

## Current Tool Call
- Tool: {tool_name}
- Arguments: {args}
- Result: {result}

## Review Rules

1. **Verify factual correctness**: The result content must be based on facts the agent actually read (shown in audit trail). If the agent wrote values, paths, URLs, API endpoints, field names, or configuration keys WITHOUT reading them first in this session, flag it.
2. **Protected files**: The following files require explicit user authorization visible in the audit trail. If the agent writes to them and the audit trail shows no user message requesting this change, flag it.
{protected_list}
3. **Consistency check**: If the agent read a file and then wrote/patched it, verify the write is consistent with what was read and with the user's intent.
4. **No hallucination in values**: Specific values (API keys, URLs, port numbers, paths, model names, config field names) must match what appears in the audit trail. Invented values are hallucinations.

## Response Format

If everything looks correct, respond with exactly:
PASS

If you find problems, respond with exactly:
FAIL
Reason: <one sentence explaining the issue>
Fix: <one sentence suggesting how to fix it>

Be concise. Do not explain what looks correct — only flag problems."""
