"""Tests for MCP configuration loader."""

import json

from nerdvana_cli.mcp.config import McpServerConfig, load_mcp_config


class TestLoadMcpConfigNoConfig:
    """When no config files exist, return an empty dict."""

    def test_no_config_files(self, tmp_path):
        result = load_mcp_config(
            cwd=str(tmp_path),
            global_path=str(tmp_path / "nonexistent" / "mcp.json"),
        )
        assert result == {}


class TestLoadMcpConfigProject:
    """Load a project-level .mcp.json with stdio server."""

    def test_project_stdio_config(self, tmp_path):
        config = {
            "mcpServers": {
                "my-server": {
                    "command": "node",
                    "args": ["/path/to/server.js"],
                    "env": {"KEY": "value"},
                }
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(config))

        result = load_mcp_config(cwd=str(tmp_path), global_path=str(tmp_path / "no.json"))
        assert "my-server" in result

        srv = result["my-server"]
        assert isinstance(srv, McpServerConfig)
        assert srv.name == "my-server"
        assert srv.transport == "stdio"
        assert srv.command == "node"
        assert srv.args == ["/path/to/server.js"]
        assert srv.env == {"KEY": "value"}


class TestLoadMcpConfigHttp:
    """Load an HTTP-type MCP server configuration."""

    def test_http_server_config(self, tmp_path):
        config = {
            "mcpServers": {
                "remote-api": {
                    "type": "http",
                    "url": "https://api.example.com/mcp",
                    "headers": {"Authorization": "Bearer token123"},
                }
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(config))

        result = load_mcp_config(cwd=str(tmp_path), global_path=str(tmp_path / "no.json"))
        srv = result["remote-api"]
        assert srv.transport == "http"
        assert srv.url == "https://api.example.com/mcp"
        assert srv.headers == {"Authorization": "Bearer token123"}
        assert srv.command == ""


class TestLoadMcpConfigEnvExpansion:
    """Environment variable references ${VAR} are expanded in env values."""

    def test_env_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "s3cret")
        monkeypatch.setenv("MY_PORT", "8080")

        config = {
            "mcpServers": {
                "test": {
                    "command": "python",
                    "args": ["server.py"],
                    "env": {
                        "ACCESS_KEY": "${MY_SECRET}",
                        "PORT": "${MY_PORT}",
                        "LITERAL": "no-expansion-here",
                        "MISSING": "${UNDEFINED_VAR_XYZ}",
                    },
                }
            }
        }
        (tmp_path / ".mcp.json").write_text(json.dumps(config))

        result = load_mcp_config(cwd=str(tmp_path), global_path=str(tmp_path / "no.json"))
        srv = result["test"]
        assert srv.env["ACCESS_KEY"] == "s3cret"
        assert srv.env["PORT"] == "8080"
        assert srv.env["LITERAL"] == "no-expansion-here"
        assert srv.env["MISSING"] == ""


class TestLoadMcpConfigMerge:
    """Global and project configs are merged."""

    def test_merge_global_and_project(self, tmp_path):
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        global_config = {
            "mcpServers": {
                "global-only": {
                    "command": "global-cmd",
                    "args": [],
                },
                "shared": {
                    "command": "global-shared",
                    "args": ["--global"],
                },
            }
        }
        (global_dir / "mcp.json").write_text(json.dumps(global_config))

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        project_config = {
            "mcpServers": {
                "project-only": {
                    "command": "project-cmd",
                    "args": [],
                },
            }
        }
        (project_dir / ".mcp.json").write_text(json.dumps(project_config))

        result = load_mcp_config(
            cwd=str(project_dir),
            global_path=str(global_dir / "mcp.json"),
        )
        assert "global-only" in result
        assert "shared" in result
        assert "project-only" in result
        assert len(result) == 3


class TestLoadMcpConfigOverride:
    """Project config overrides global config for the same server name."""

    def test_project_overrides_global(self, tmp_path):
        global_dir = tmp_path / "global"
        global_dir.mkdir()
        global_config = {
            "mcpServers": {
                "server-a": {
                    "command": "global-cmd",
                    "args": ["--global"],
                }
            }
        }
        (global_dir / "mcp.json").write_text(json.dumps(global_config))

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        project_config = {
            "mcpServers": {
                "server-a": {
                    "command": "project-cmd",
                    "args": ["--project"],
                }
            }
        }
        (project_dir / ".mcp.json").write_text(json.dumps(project_config))

        result = load_mcp_config(
            cwd=str(project_dir),
            global_path=str(global_dir / "mcp.json"),
        )
        assert result["server-a"].command == "project-cmd"
        assert result["server-a"].args == ["--project"]
