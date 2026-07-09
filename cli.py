"""CLI subcommands for harness-guard plugin.

Provides:
  hermes guard init   — scan project and generate .hermes-guard.yaml
  hermes guard review — show recent review stats from audit log
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .audit_log import get_log
from .project_config import (
    _CONFIG_FILENAME,
    generate_guard_config,
    load_project_config,
)


def _cmd_init(args: argparse.Namespace) -> int:
    """Generate .hermes-guard.yaml in the current directory."""
    target = Path(args.path or os.getcwd()).resolve()

    # Check if already exists
    existing = target / _CONFIG_FILENAME
    if existing.exists() and not args.force:
        print(f"✗ {_CONFIG_FILENAME} 已存在: {existing}")
        print("  使用 --force 覆盖")
        return 1

    yaml_content = generate_guard_config(str(target))
    existing.write_text(yaml_content, encoding="utf-8")
    print(f"✓ 已生成 {existing}")
    print()
    print("请审查内容，按需修改后提交到版本控制。")

    # Show the config
    print("─" * 40)
    for line in yaml_content.splitlines():
        print(f"  {line}")
    return 0


def _cmd_review(args: argparse.Namespace) -> int:
    """Show recent review stats."""
    log = get_log()
    proj_cfg = load_project_config(args.path)
    config_path = _find_config_path(args.path)

    print("harness-guard 状态")
    print("─" * 40)

    if config_path:
        print(f"项目配置: {config_path}")
        if proj_cfg:
            pf = proj_cfg.get("protected_files", [])
            cr = proj_cfg.get("custom_rules", [])
            dc = proj_cfg.get("dangerous_commands", [])
            if pf:
                print(f"  受保护文件: {len(pf)} 个")
            if cr:
                print(f"  自定义规则: {len(cr)} 条")
            if dc:
                print(f"  危险命令: {len(dc)} 条")
    else:
        print("项目配置: 未找到 .hermes-guard.yaml (使用全局默认)")

    print()
    print(f"审计日志: {log.total_entries()} 条 (全局)")

    # Count per-session
    sessions = log.session_summary()
    if sessions:
        print(f"活跃会话: {len(sessions)}")
        for sid, count in list(sessions.items())[:5]:
            print(f"  {sid[:16]}: {count} 条记录")
    else:
        print("活跃会话: 无")

    return 0


def _find_config_path(project_dir: str | None = None) -> str | None:
    """Find the .hermes-guard.yaml path, or None."""
    from .project_config import _find_project_root
    root = _find_project_root(project_dir)
    if root:
        return str(root / _CONFIG_FILENAME)
    return None


def _setup_parser(parser: argparse.ArgumentParser) -> None:
    """Add subcommands to the hermes guard parser."""
    sub = parser.add_subparsers(dest="guard_command", help="harness-guard 子命令")

    # init
    p_init = sub.add_parser("init", help="扫描项目并生成 .hermes-guard.yaml")
    p_init.add_argument(
        "--path", "-p",
        default=None,
        help="项目路径 (默认: 当前目录)",
    )
    p_init.add_argument(
        "--force", "-f",
        action="store_true",
        help="覆盖已有的 .hermes-guard.yaml",
    )
    p_init.set_defaults(func=_cmd_init)

    # review
    p_review = sub.add_parser("review", help="显示审查统计和项目配置状态")
    p_review.add_argument(
        "--path", "-p",
        default=None,
        help="项目路径 (默认: 当前目录)",
    )
    p_review.set_defaults(func=_cmd_review)


def _handle_command(args: argparse.Namespace) -> int:
    """Dispatch to the selected subcommand."""
    func = getattr(args, "func", None)
    if func is None:
        print("用法: hermes guard <init|review>")
        return 1
    return func(args)


def register_guard_cli(ctx) -> None:
    """Register the 'guard' CLI subcommand with Hermes."""
    ctx.register_cli_command(
        name="guard",
        help="harness-guard 项目配置管理",
        setup_fn=_setup_parser,
        handler_fn=_handle_command,
        description="管理 harness-guard 的项目级配置。使用 'init' 生成配置，'review' 查看状态。",
    )
