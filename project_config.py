"""Per-project guardrail configuration.

Reads ``.hermes-guard.yaml`` from the current working directory and merges
project-level rules into the global defaults.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_CONFIG_FILENAME = ".hermes-guard.yaml"


def _find_project_root(start: str | None = None) -> Path | None:
    """Walk up from *start* looking for the config file, stop at git root or HOME."""
    current = Path(start or os.getcwd()).resolve()
    home = Path.home().resolve()
    while current != home and current != current.parent:
        candidate = current / _CONFIG_FILENAME
        if candidate.exists():
            return current
        # Stop at git root
        if (current / ".git").exists():
            # Check one more level (the root itself)
            candidate = current / _CONFIG_FILENAME
            if candidate.exists():
                return current
            break
        current = current.parent
    return None


def load_project_config(project_dir: str | None = None) -> dict[str, Any]:
    """Load and return the project-level config dict, or empty dict if not found."""
    root = _find_project_root(project_dir)
    if root is None:
        return {}
    config_path = root / _CONFIG_FILENAME
    try:
        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def get_merged_protected_paths(project_config: dict[str, Any]) -> tuple[str, ...]:
    """Merge global PROTECTED_PATHS with project-level additions."""
    from .rules import PROTECTED_PATHS

    extra = project_config.get("protected_files", [])
    if not isinstance(extra, list):
        extra = []
    return tuple(PROTECTED_PATHS) + tuple(str(p) for p in extra)


def get_merged_custom_rules(project_config: dict[str, Any]) -> list[str]:
    """Get project-level custom review rules."""
    rules = project_config.get("custom_rules", [])
    if not isinstance(rules, list):
        rules = []
    return [str(r) for r in rules]


def get_merged_terminal_patterns(
    project_config: dict[str, Any],
) -> list[re.Pattern]:
    """Merge global TERMINAL_REVIEW_PATTERNS with project-level additions."""
    from .rules import TERMINAL_REVIEW_PATTERNS

    extra_strings = project_config.get("dangerous_commands", [])
    if not isinstance(extra_strings, list):
        extra_strings = []
    extra_patterns = [
        re.compile(re.escape(s), re.IGNORECASE) for s in extra_strings if s
    ]
    return list(TERMINAL_REVIEW_PATTERNS) + extra_patterns


def generate_guard_config(project_dir: str | None = None) -> str:
    """Scan a project directory and generate a .hermes-guard.yaml template.

    Returns the YAML string (not written to disk — caller decides).
    """
    root = Path(project_dir or os.getcwd()).resolve()
    protected: list[str] = []
    dangerous: list[str] = []

    # Scan for notable files
    notable_files = {
        "docker-compose.yml": "Docker Compose 配置",
        "docker-compose.yaml": "Docker Compose 配置",
        "Dockerfile": "Docker 构建",
        "nginx.conf": "Nginx 配置",
        ".env.example": "环境变量模板",
        "Makefile": "构建脚本",
        "package.json": "Node.js 项目配置",
        "pyproject.toml": "Python 项目配置",
        "Cargo.toml": "Rust 项目配置",
        "go.mod": "Go 项目配置",
    }
    for fname, desc in notable_files.items():
        if (root / fname).exists():
            protected.append(f"{fname} ({desc})")

    # Scan for Docker usage → add dangerous commands
    if (root / "docker-compose.yml").exists() or (root / "docker-compose.yaml").exists():
        dangerous.append("docker compose down")
        dangerous.append("docker compose build --no-cache")
    if (root / "Dockerfile").exists():
        dangerous.append("docker build")

    # Scan for database-related files
    db_files = ["migrations/", "seeds/", "*.sql", "knexfile.js", "alembic.ini"]
    for pattern in db_files:
        if (root / pattern).exists() or any(root.glob(pattern)):
            dangerous.append("DROP TABLE")
            dangerous.append("DELETE FROM")
            break

    # Generate YAML
    lines = [
        "# harness-guard 项目级配置",
        "# 此文件应提交到版本控制，与项目一起维护",
        "",
    ]
    if protected:
        lines.append("# 受保护文件 — 写入这些文件需要用户明确授权")
        lines.append("protected_files:")
        for p in protected:
            lines.append(f"  - {p}")
        lines.append("")
    else:
        lines.append("# 受保护文件（按需添加）")
        lines.append("protected_files: []")
        lines.append("")

    lines.append("# 自定义审查规则 — 会被追加到全局审查规则之后")
    lines.append("custom_rules: []")
    lines.append("")

    if dangerous:
        lines.append("# 额外的危险命令模式 — 匹配到时触发审查")
        lines.append("dangerous_commands:")
        for d in dangerous:
            lines.append(f'  - "{d}"')
    else:
        lines.append("# 额外的危险命令模式（按需添加）")
        lines.append("dangerous_commands: []")

    return "\n".join(lines) + "\n"
