"""Audit log for harness-guard plugin.

Thread-safe in-memory storage of tool call history, keyed by session_id.
Used by the reviewer to see "what was read -> what is being written".
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_ENTRIES_PER_SESSION = 50
_MAX_TOTAL_ENTRIES = 10000
_MAX_SUMMARY_LEN = 300


@dataclass
class AuditEntry:
    tool: str
    args_summary: str
    result_summary: str
    ts: float  # time.time()
    is_user_message: bool = False  # True for user messages injected into log


class AuditLog:
    """Thread-safe audit log storage."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: Dict[str, deque] = {}
        self._total_count = 0

    def append(self, session_id: str, entry: AuditEntry) -> None:
        with self._lock:
            dq = self._sessions.setdefault(session_id, deque(maxlen=_MAX_ENTRIES_PER_SESSION))
            dq.append(entry)
            self._total_count += 1
            if self._total_count > _MAX_TOTAL_ENTRIES:
                self._evict()

    def _evict(self) -> None:
        """Evict oldest entries from the oldest session when total exceeds cap."""
        evict_target = self._total_count - int(_MAX_TOTAL_ENTRIES * 0.8)
        evicted = 0
        for sid in list(self._sessions.keys()):
            if evicted >= evict_target:
                break
            dq = self._sessions[sid]
            while dq and evicted < evict_target:
                dq.popleft()
                evicted += 1
            if not dq:
                del self._sessions[sid]
        self._total_count -= evicted

    def get_recent(self, session_id: str, limit: int = 20) -> List[AuditEntry]:
        with self._lock:
            dq = self._sessions.get(session_id)
            if not dq:
                return []
            return list(dq)[-limit:]

    def clear_session(self, session_id: str) -> None:
        with self._lock:
            dq = self._sessions.pop(session_id, None)
            if dq:
                self._total_count -= len(dq)

    def total_entries(self) -> int:
        with self._lock:
            return self._total_count

    def session_summary(self) -> dict[str, int]:
        with self._lock:
            return {sid: len(dq) for sid, dq in self._sessions.items() if dq}


# Module-level singleton
_log = AuditLog()


def get_log() -> AuditLog:
    return _log


def summarize_args(args: Any, max_len: int = _MAX_SUMMARY_LEN) -> str:
    """Summarize tool call args for audit log."""
    if not args or not isinstance(args, dict):
        return ""
    parts = []
    for k, v in args.items():
        if k in ("task_id", "session_id", "tool_call_id", "turn_id", "api_request_id"):
            continue
        s = str(v)
        if len(s) > 200:
            s = s[:200] + "..."
        parts.append(f"{k}={s}")
    result = " | ".join(parts)
    if len(result) > max_len:
        result = result[:max_len] + "..."
    return result


def summarize_result(result: Any, max_len: int = _MAX_SUMMARY_LEN) -> str:
    """Summarize tool result for audit log."""
    if not result:
        return ""
    s = str(result)
    if len(s) > max_len:
        s = s[:max_len] + "..."
    return s
