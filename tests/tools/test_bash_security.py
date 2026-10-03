"""Tests for BashTool dangerous command blocking."""
from nerdvana_cli.core.tool import ToolContext
from nerdvana_cli.tools.bash_tool import BashArgs, BashTool
from nerdvana_cli.types import PermissionBehavior


class TestBashDangerousCommands:
    def setup_method(self):
        self.tool = BashTool()
        self.ctx = ToolContext(cwd="/tmp")

    def test_rm_rf_root_blocked(self):
        result = self.tool.check_permissions(BashArgs(command="rm -rf /"), self.ctx)
        assert result.behavior == PermissionBehavior.DENY

    def test_rm_rf_home_blocked(self):
        result = self.tool.check_permissions(BashArgs(command="rm -rf ~/"), self.ctx)
        assert result.behavior == PermissionBehavior.DENY

    def test_rm_rf_dot_blocked(self):
        result = self.tool.check_permissions(BashArgs(command="rm -rf ."), self.ctx)
        assert result.behavior == PermissionBehavior.DENY

    def test_chmod_777_blocked(self):
        result = self.tool.check_permissions(BashArgs(command="chmod 777 /etc"), self.ctx)
        assert result.behavior == PermissionBehavior.DENY

    def test_shutdown_blocked(self):
        result = self.tool.check_permissions(BashArgs(command="shutdown -h now"), self.ctx)
        assert result.behavior == PermissionBehavior.DENY

    def test_reboot_blocked(self):
        result = self.tool.check_permissions(BashArgs(command="reboot"), self.ctx)
        assert result.behavior == PermissionBehavior.DENY

    def test_safe_rm_allowed(self):
        result = self.tool.check_permissions(BashArgs(command="rm temp.txt"), self.ctx)
        assert result.behavior == PermissionBehavior.ALLOW

    def test_safe_command_allowed(self):
        result = self.tool.check_permissions(BashArgs(command="ls -la"), self.ctx)
        assert result.behavior == PermissionBehavior.ALLOW

    def test_git_push_force_blocked(self):
        result = self.tool.check_permissions(BashArgs(command="git push --force"), self.ctx)
        assert result.behavior == PermissionBehavior.DENY
