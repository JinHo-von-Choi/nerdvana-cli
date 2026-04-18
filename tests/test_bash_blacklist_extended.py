"""Extended BashTool blacklist regression tests — M-3 substitution bypass patterns.

Covers:
- Command substitution: $(rm -rf ...), $(mkfs), $(dd if=), $(shutdown)
- Variable substitution: ${rm ...}  (rare but valid bash)
- Backtick substitution: `rm -rf ...`, `mkfs`, `shutdown`
- eval/exec bypass vectors
- dd of=/dev/... (write side)
- env-prefix sudo bypass: FOO=1 sudo rm ...
- timeout ceiling enforcement (MAX_TIMEOUT=600)
- No false-positive regressions on common safe commands

작성자: 최진호
작성일: 2026-04-18
"""

from __future__ import annotations

import pytest

from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.bash_tool import BashArgs, BashTool
from nerdvana_cli.types import PermissionBehavior


@pytest.fixture()
def tool() -> BashTool:
    return BashTool()


@pytest.fixture()
def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


def _is_denied(tool: BashTool, ctx: ToolContext, cmd: str) -> bool:
    return tool.check_permissions(BashArgs(command=cmd), ctx).behavior == PermissionBehavior.DENY


def _is_allowed(tool: BashTool, ctx: ToolContext, cmd: str) -> bool:
    return tool.check_permissions(BashArgs(command=cmd), ctx).behavior == PermissionBehavior.ALLOW


# ---------------------------------------------------------------------------
# Command substitution $(...) — dangerous inner commands
# ---------------------------------------------------------------------------

def test_cmd_sub_rm_rf(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "echo $(rm -rf /tmp/data)")


def test_cmd_sub_rm_rf_root(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "result=$(rm -rf /)")


def test_cmd_sub_mkfs(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "$(mkfs /dev/sda)")


def test_cmd_sub_dd_if(tool: BashTool, ctx: ToolContext) -> None:
    # Reading from a block device inside $(...) is dangerous
    assert _is_denied(tool, ctx, "x=$(dd if=/dev/sda of=/tmp/disk.img bs=512)")


def test_cmd_sub_shutdown(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "$(shutdown -h now)")


def test_cmd_sub_reboot(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "out=$(reboot)")


# ---------------------------------------------------------------------------
# Variable substitution ${...} — dangerous inner content
# ---------------------------------------------------------------------------

def test_var_sub_rm_rf(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "${rm -rf /home}")


# ---------------------------------------------------------------------------
# Backtick substitution `...` — dangerous inner commands
# ---------------------------------------------------------------------------

def test_backtick_rm_rf(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "echo `rm -rf /`")


def test_backtick_mkfs(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "`mkfs.ext4 /dev/sdb`")


def test_backtick_shutdown(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "`shutdown now`")


def test_backtick_halt(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "`halt`")


def test_backtick_rm_rf_subdir(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "x=`rm -r -f /tmp/work`")


# ---------------------------------------------------------------------------
# eval / exec bypass
# ---------------------------------------------------------------------------

def test_eval_blocked(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "eval 'rm -rf /'")


def test_eval_var_blocked(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "eval $CMD")


def test_exec_blocked(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "exec rm -rf /tmp")


def test_exec_shell_blocked(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "exec bash -c 'rm -rf /'")


# ---------------------------------------------------------------------------
# dd of= — write side of dd
# ---------------------------------------------------------------------------

def test_dd_of_block_device(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "dd if=/dev/zero of=/dev/sda")


def test_dd_of_nvme(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "dd if=backup.img of=/dev/nvme0n1 bs=512")


def test_dd_of_regular_file_allowed(tool: BashTool, ctx: ToolContext) -> None:
    # Writing to a regular file is not a block device — must NOT be blocked.
    assert _is_allowed(tool, ctx, "dd if=/dev/urandom of=/tmp/random.bin bs=1M count=1")


# ---------------------------------------------------------------------------
# env-prefix sudo bypass: FOO=1 sudo rm ...
# ---------------------------------------------------------------------------

def test_env_prefix_sudo_rm(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "HOME=/tmp sudo rm -rf /")


def test_env_prefix_sudo_reboot(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "FOO=bar sudo reboot")


def test_multi_env_prefix_sudo(tool: BashTool, ctx: ToolContext) -> None:
    assert _is_denied(tool, ctx, "A=1 B=2 sudo shutdown now")


# ---------------------------------------------------------------------------
# Timeout ceiling enforcement
# ---------------------------------------------------------------------------

def test_timeout_clamped_to_max(tool: BashTool, ctx: ToolContext) -> None:
    args = BashArgs(command="sleep 1", timeout=999999)
    tool.check_permissions(args, ctx)
    assert args.timeout == tool._MAX_TIMEOUT


def test_timeout_below_max_unchanged(tool: BashTool, ctx: ToolContext) -> None:
    args = BashArgs(command="sleep 1", timeout=30)
    tool.check_permissions(args, ctx)
    assert args.timeout == 30


def test_timeout_at_max_unchanged(tool: BashTool, ctx: ToolContext) -> None:
    args = BashArgs(command="sleep 1", timeout=600)
    tool.check_permissions(args, ctx)
    assert args.timeout == 600


# ---------------------------------------------------------------------------
# False-positive regression — safe commands must NOT be blocked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "ls -la",
    "git status",
    "npm install",
    "python -m pytest tests/",
    "cat README.md",
    "echo hello",
    "grep -r 'pattern' src/",
    "find . -name '*.py'",
    "docker ps",
    "git push origin main",
    "curl https://api.example.com/health",
])
def test_safe_commands_not_blocked(tool: BashTool, ctx: ToolContext, cmd: str) -> None:
    result = tool.check_permissions(BashArgs(command=cmd), ctx)
    assert result.behavior != PermissionBehavior.DENY, (
        f"False positive: safe command '{cmd}' was blocked"
    )
