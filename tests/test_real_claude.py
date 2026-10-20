"""Checks this tool's assumptions against the REAL Claude Code CLI (macOS/Linux).

Opt-in, because it runs the real `claude` program (always with a throwaway home folder):
    CCA_REAL_CLAUDE=1 python -m unittest tests.test_real_claude -v
CI runs it weekly so a Claude Code update that breaks an assumption shows up quickly.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox as sb  # noqa: E402

SKIP = None
if sb.IS_WIN:
    SKIP = "macOS/Linux only"
elif not os.environ.get("CCA_REAL_CLAUDE"):
    SKIP = "set CCA_REAL_CLAUDE=1 to run against the real Claude Code"
elif not shutil.which("claude"):
    SKIP = "claude is not installed"


@unittest.skipIf(SKIP, SKIP)
class RealClaudeCode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.claude = shutil.which("claude")          # resolve before any fake is on PATH
        cls.root, cls.home = sb.make_home("O'Brien real")
        (cls.home / ".claude").mkdir()
        (cls.home / ".claude" / "settings.json").write_text('{"theme": "dark"}')
        cls.ca = sb.load_cli(cls.home)
        cls.a2 = cls.ca.account_dir(2)
        cls.cc(1, "mcp", "add", "-s", "user", "work-mcp", "--", "echo", "hi")
        sb.quiet(cls.ca.cmd_setup, 2)

        mkt = cls.root / "mkt"                       # a tiny local plugin marketplace
        (mkt / ".claude-plugin").mkdir(parents=True)
        (mkt / "p1" / ".claude-plugin").mkdir(parents=True)
        (mkt / ".claude-plugin" / "marketplace.json").write_text(json.dumps(
            {"name": "localmkt", "owner": {"name": "t"},
             "plugins": [{"name": "p1", "source": "./p1", "description": "test"}]}))
        (mkt / "p1" / ".claude-plugin" / "plugin.json").write_text(json.dumps(
            {"name": "p1", "version": "0.0.1", "description": "test"}))
        cls.mkt = mkt

    @classmethod
    def tearDownClass(cls):
        sb.cleanup(cls.root)

    @classmethod
    def cc(cls, account, *args):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        env["HOME"] = str(cls.home)
        if account != 1:
            env["CLAUDE_CONFIG_DIR"] = str(cls.ca.account_dir(account))
        r = subprocess.run([cls.claude, *args], env=env, capture_output=True, text=True,
                           timeout=180)
        return r.stdout + r.stderr

    def test_1_accounts_start_isolated(self):
        self.assertIn("No MCP server", self.cc(2, "mcp", "get", "work-mcp"))

    def test_2_config_lives_in_the_account_folder(self):
        self.cc(2, "mcp", "add", "-s", "user", "own-mcp", "--", "echo", "hi")
        self.assertTrue((self.a2 / ".claude.json").exists())
        self.assertIn("No MCP server", self.cc(1, "mcp", "get", "own-mcp"))

    def test_3_sync_mcp_is_read_by_claude_code(self):
        sb.quiet(self.ca.cmd_sync_mcp, 2)
        self.assertIn("User config", self.cc(2, "mcp", "get", "work-mcp"))
        self.assertIn("User config", self.cc(2, "mcp", "get", "own-mcp"))

    def test_4_settings_written_through_the_link(self):
        self.cc(2, "plugin", "marketplace", "add", str(self.mkt))
        self.cc(2, "plugin", "install", "p1@localmkt")
        self.assertTrue((self.a2 / "settings.json").is_symlink(),
                        "Claude Code replaced the linked settings.json - sharing would break")
        self.assertIn("p1@localmkt", (self.home / ".claude" / "settings.json").read_text())

    def test_5_plugin_from_account_2_active_in_account_1(self):
        self.assertIn("p1@localmkt", self.cc(1, "plugin", "list"))

    def test_6_all_links_still_healthy(self):
        self.assertNotIn("NOT LINKED", sb.quiet(self.ca.cmd_status))


if __name__ == "__main__":
    unittest.main()
