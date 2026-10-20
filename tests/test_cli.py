"""Tests for claude_accounts.py on macOS/Linux (real Windows is covered by test_windows.py).

Run from the repo root:  python -m unittest discover -s tests -v
"""
import codecs
import json
import os
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox as sb  # noqa: E402

OLD_FOLDERS = ["skills", "agents", "commands", "plugins", "hooks", "projects", "todos",
               "output-styles"]
OLD_FILES = ["CLAUDE.md", "settings.json"]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.home = sb.make_home()
        cls.ca = sb.load_cli(cls.home)
        cls.main = cls.ca.MAIN_DIR
        (cls.main / "skills" / "s1").mkdir(parents=True)
        (cls.main / "settings.json").write_text("{}")
        (cls.main / "projects" / "projA").mkdir(parents=True)
        (cls.main / "projects" / "projA" / "s1.jsonl").write_text("A1")

    @classmethod
    def tearDownClass(cls):
        sb.cleanup(cls.root)

    def linked_count(self, n):
        d = self.ca.account_dir(n)
        return sum(self.ca.same_target(d / x, self.main / x) for x, _ in self.ca.shared_items())


@unittest.skipIf(sb.IS_WIN, "covered by test_windows.py")
class SetupAndSharing(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        a2 = cls.ca.account_dir(2)
        (a2 / "skills" / "own").mkdir(parents=True)                # account 2 already used
        (a2 / "CLAUDE.md").write_text("mine")
        os.symlink(cls.home / "nowhere", a2 / "todos")              # broken link
        cls.out = sb.quiet(cls.ca.cmd_setup, 2)
        cls.a2 = a2

    def test_links_every_shared_item(self):
        self.assertEqual(self.linked_count(2), len(self.ca.shared_items()))

    def test_existing_items_backed_up_not_lost(self):
        self.assertTrue(any((p / "own").exists() for p in self.a2.glob("skills.bak-*")))
        self.assertTrue(any(p.read_text() == "mine" for p in self.a2.glob("CLAUDE.md.bak-*")))

    def test_broken_link_repaired(self):
        self.assertTrue(self.ca.same_target(self.a2 / "todos", self.main / "todos"))

    def test_sharing_works_both_ways(self):
        (self.a2 / "projects" / "new-chat.jsonl").write_text("x")
        self.assertTrue((self.main / "projects" / "new-chat.jsonl").exists())
        (self.main / "settings.json").write_text('{"a": 2}')
        self.assertIn("2", (self.a2 / "settings.json").read_text())

    def test_never_links_login_or_account_files(self):
        for name in (".credentials.json", ".claude.json", "remote-settings.json",
                     "policy-limits.json"):
            self.assertFalse(self.ca.is_link_or_junction(self.a2 / name), name)

    def test_rerun_is_idempotent(self):
        before = len(list(self.a2.glob("*.bak-*")))
        out = sb.quiet(self.ca.cmd_setup, 2)
        self.assertEqual(len(list(self.a2.glob("*.bak-*"))), before)
        self.assertNotIn("backup", out)

    def test_shortcuts_written_once(self):
        sb.quiet(self.ca.write_shortcuts)
        text = (self.home / ".zshrc").read_text()
        self.assertEqual(text.count(self.ca.BLOCK_START), 1)
        self.assertIn("alias claude2=", text)


@unittest.skipIf(sb.IS_WIN, "covered by test_windows.py")
class UpgradeAndMerge(Base):
    """An account set up by an older release (10 items) upgrades cleanly."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        old = sb.load_cli(cls.home, alias="claude_accounts_old")
        old.SHARED_FOLDERS, old.SHARED_FILES, old.OPTIONAL_FILES = OLD_FOLDERS, OLD_FILES, []
        sb.quiet(old.cmd_setup, 2)
        a2 = cls.a2 = cls.ca.account_dir(2)
        (a2 / "rules").mkdir()
        (a2 / "rules" / "own-rule.md").write_text("R2")
        (a2 / "plans").mkdir()
        (a2 / "plans" / "p2.md").write_text("plan")
        (a2 / "file-history" / "sid9").mkdir(parents=True)
        (a2 / "file-history" / "sid9" / "f").write_text("ckpt")
        (a2 / "history.jsonl").write_text('{"display":"old prompt"}\n')
        a3 = cls.a3 = cls.ca.account_dir(3)
        (a3 / "projects" / "projA").mkdir(parents=True)
        (a3 / "projects" / "projA" / "s1.jsonl").write_text("CONFLICT")
        (a3 / "projects" / "projA" / "s2.jsonl").write_text("A3")
        (a3 / "projects" / "projB").mkdir()
        (a3 / "projects" / "projB" / "s3.jsonl").write_text("B3")
        cls.before = sum(cls.ca.same_target(a2 / x, cls.main / x) for x, _ in cls.ca.shared_items())
        sb.quiet(cls.ca.cmd_setup, 2)
        sb.quiet(cls.ca.cmd_setup, 3)

    def test_old_release_had_fewer_links(self):
        self.assertEqual(self.before, 10)

    def test_upgrade_links_everything(self):
        self.assertEqual(self.linked_count(2), len(self.ca.shared_items()))

    def test_work_records_merged_into_account_1(self):
        self.assertTrue((self.main / "plans" / "p2.md").exists())
        self.assertTrue((self.main / "file-history" / "sid9" / "f").exists())
        self.assertEqual((self.main / "projects" / "projA" / "s2.jsonl").read_text(), "A3")
        self.assertTrue((self.main / "projects" / "projB" / "s3.jsonl").exists())

    def test_merge_conflict_keeps_account_1_and_backs_up_the_other(self):
        self.assertEqual((self.main / "projects" / "projA" / "s1.jsonl").read_text(), "A1")
        self.assertTrue(any((p / "projA" / "s1.jsonl").read_text() == "CONFLICT"
                            for p in self.a3.glob("projects.bak-*")))

    def test_config_folders_backed_up_not_merged(self):
        self.assertFalse((self.main / "rules" / "own-rule.md").exists())
        self.assertTrue(any((p / "own-rule.md").exists() for p in self.a2.glob("rules.bak-*")))

    def test_prompt_history_backed_up(self):
        self.assertTrue(any(p.read_text().startswith('{"display":"old')
                            for p in self.a2.glob("history.jsonl.bak-*")))


@unittest.skipIf(sb.IS_WIN, "covered by test_windows.py")
class OptionalAndBackups(Base):
    def test_keybindings_linked_only_once_it_exists(self):
        names = [x for x, _ in self.ca.shared_items()]
        self.assertNotIn("keybindings.json", names)
        (self.main / "keybindings.json").write_text("[]")
        sb.quiet(self.ca.cmd_setup, 2)
        a2 = self.ca.account_dir(2)
        self.assertTrue(self.ca.same_target(a2 / "keybindings.json", self.main / "keybindings.json"))

    def test_backups_in_same_second_never_collide(self):
        f = self.root / "x.json"
        names = []
        for i in range(3):
            f.write_text(f"v{i}")
            names.append(self.ca.backup(f))
        self.assertEqual(len(set(names)), 3)
        self.assertEqual([n.read_text() for n in names], ["v0", "v1", "v2"])


class ProfileEncodings(Base):
    """Writing the PowerShell profile must keep its encoding and line endings exactly."""

    def write(self, raw: bytes) -> bytes:
        p = self.root / "prof.ps1"
        p.write_bytes(raw)
        ca = self.ca
        was, ca.IS_WINDOWS = ca.IS_WINDOWS, True
        ca.powershell_profiles = lambda: [p]
        try:
            sb.quiet(ca.write_shortcuts)
            sb.quiet(ca.write_shortcuts)        # second run exercises the replace path
        finally:
            ca.IS_WINDOWS = was
        return p.read_bytes()

    def test_utf8_bom_and_hindi_preserved(self):
        out = self.write(codecs.BOM_UTF8 + "# नमस्ते prompt\r\n".encode("utf-8"))
        self.assertTrue(out.startswith(codecs.BOM_UTF8))
        self.assertIn("नमस्ते", out.decode("utf-8-sig"))
        self.assertNotIn(b"\r\r", out)

    def test_utf16_preserved(self):
        out = self.write("# café\r\n".encode("utf-16"))
        self.assertTrue(out.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)))
        self.assertIn("café", out.decode("utf-16"))

    def test_legacy_code_page_bytes_untouched(self):
        legacy = "# Grüße\r\n".encode("cp1252")
        out = self.write(legacy)
        self.assertTrue(out.startswith(legacy))
        self.assertIn(self.ca.BLOCK_START.encode(), out)

    def test_plain_utf8_gets_no_bom(self):
        out = self.write(b"# plain\r\n")
        self.assertFalse(out.startswith(codecs.BOM_UTF8))

    def test_block_written_once_and_backslashes_survive(self):
        out = self.write(b"Set-Alias ll ls\r\n").decode("utf-8")
        self.assertEqual(out.count(self.ca.BLOCK_START), 1)
        self.assertIn("Set-Alias ll ls", out)
        self.assertIn("Env:\\CLAUDE_CONFIG_DIR", out)


@unittest.skipIf(sb.IS_WIN, "real junctions are tested in test_windows.py")
class WindowsJunctionCalls(Base):
    def setUp(self):
        self.ca.IS_WINDOWS = True

    def tearDown(self):
        self.ca.IS_WINDOWS = False
        sys.modules.pop("_winapi", None)
        self.ca.subprocess = subprocess

    def test_junction_made_by_windows_api(self):
        calls = []
        src, dst = self.root / "R&D src", self.root / "R&D dst"
        src.mkdir()
        sys.modules["_winapi"] = types.SimpleNamespace(
            CreateJunction=lambda s, t: (calls.append((s, t)), os.symlink(s, t, True)))
        self.assertEqual(self.ca.link_folder(src, dst), "junction")
        self.assertEqual(calls, [(str(src), str(dst))])

    def test_fallback_quotes_ampersand_paths_for_cmd(self):
        seen = []
        sys.modules["_winapi"] = types.SimpleNamespace()
        self.ca.subprocess = types.SimpleNamespace(run=lambda c, **k: (
            seen.append(c), types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1])
        dst = self.root / "R&D dst2"
        self.ca.link_folder(self.root, dst)
        self.assertIsInstance(seen[0], str)
        self.assertIn(f'"{dst}"', seen[0])


class ExecutionPolicy(Base):
    def run_policy(self, answers):
        ca, seq = self.ca, iter(answers)
        ca.IS_WINDOWS = True
        ca.shutil = types.SimpleNamespace(which=lambda e: "powershell.exe")
        ca.subprocess = types.SimpleNamespace(run=lambda c, **k: types.SimpleNamespace(
            returncode=0, stdout=next(seq) if "Get-ExecutionPolicy" in c[-1] else "", stderr=""))
        try:
            return sb.quiet(ca.check_execution_policy)
        finally:
            ca.IS_WINDOWS, ca.subprocess, ca.shutil = False, subprocess, shutil

    def test_group_policy_override_reported_honestly(self):
        out = self.run_policy(["Restricted", "Restricted"])
        self.assertIn("Group Policy blocks", out)
        self.assertNotIn("done", out)

    def test_policy_change_confirmed(self):
        self.assertIn("done", self.run_policy(["Restricted", "RemoteSigned"]))

    def test_no_question_when_already_allowed(self):
        self.assertEqual(self.run_policy(["RemoteSigned"]), "")


@unittest.skipIf(sb.IS_WIN, "covered by test_windows.py")
class SyncMcp(Base):
    def test_sync_flow(self):
        ca = self.ca
        (self.home / ".claude.json").write_text('{"mcpServers": {"github": {"command": "gh"}}}')
        sb.quiet(ca.cmd_setup, 2)
        with self.assertRaises(SystemExit):           # account 2 not logged in yet
            sb.quiet(ca.cmd_sync_mcp, 2)
        cfg = ca.account_dir(2) / ".claude.json"
        cfg.write_text('{"theme": "dark", "mcpServers": {"own": {"command": "x"}}}')
        self.assertIn("github", sb.quiet(ca.cmd_sync_mcp, 2))
        data = json.loads(cfg.read_text())
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(set(data["mcpServers"]), {"github", "own"})
        self.assertTrue(list(cfg.parent.glob(".claude.json.bak-*")))


@unittest.skipIf(sb.IS_WIN, "covered by test_windows.py")
class StatusUndoAndSafety(Base):
    def test_status_detects_broken_link_and_setup_repairs(self):
        ca = self.ca
        sb.quiet(ca.cmd_setup, 2)
        f = ca.account_dir(2) / "settings.json"
        f.unlink()
        f.write_text("{}")
        self.assertIn("NOT LINKED", sb.quiet(ca.cmd_status))
        sb.quiet(ca.cmd_setup, 2)
        self.assertNotIn("NOT LINKED", sb.quiet(ca.cmd_status))

    def test_answering_no_changes_nothing(self):
        ca = self.ca
        ca.ask_yes = lambda q: False
        try:
            sb.quiet(ca.cmd_setup, 7)
        finally:
            ca.ask_yes = lambda q: True
        self.assertFalse(ca.account_dir(7).exists())

    def test_refuses_to_set_up_account_1(self):
        with self.assertRaises(SystemExit):
            sb.quiet(self.ca.cmd_setup, 1)

    def test_undo_restores_and_never_touches_account_1(self):
        ca = self.ca
        a5 = ca.account_dir(5)
        (a5 / "rules").mkdir(parents=True)
        (a5 / "rules" / "mine.md").write_text("R5")
        sb.quiet(ca.cmd_setup, 5)
        answers = iter([True, False])
        ca.ask_yes = lambda q: next(answers)
        sb.quiet(ca.cmd_undo, 5)
        self.assertTrue((a5 / "rules" / "mine.md").exists())
        self.assertTrue((self.main / "skills" / "s1").exists())
        self.assertEqual((self.main / "projects" / "projA" / "s1.jsonl").read_text(), "A1")
        ca.ask_yes = lambda q: True
        sb.quiet(ca.cmd_undo, 5)                    # second time: delete folder too
        self.assertFalse(a5.exists())
        self.assertTrue((self.main / "skills" / "s1").exists())
        self.assertNotIn("claude5", (self.home / ".zshrc").read_text())


@unittest.skipIf(sb.IS_WIN, "covered by test_windows.py")
class RealShells(Base):
    """Runs the generated shortcuts in real zsh / PowerShell when they're installed."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sb.fake_claude(cls.root / "bin")
        sb.quiet(cls.ca.cmd_setup, 2)

    @unittest.skipUnless(shutil.which("zsh"), "zsh not installed")
    def test_zsh_aliases(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        out = subprocess.run(["zsh", "-ic", "claude2 --continue; claude1"], env=env,
                             capture_output=True, text=True, timeout=60).stdout
        lines = [x for x in out.splitlines() if x.startswith("DIR=")]
        self.assertEqual(len(lines), 2, out)
        self.assertTrue(lines[0].startswith(f"DIR={self.home}/.claude-account2|"))
        self.assertTrue(lines[0].endswith("ARGS=--continue"))
        self.assertTrue(lines[1].startswith("DIR=DEFAULT|"))

    @unittest.skipUnless(sb.powershell(), "PowerShell not installed")
    def test_powershell_functions(self):
        ca = self.ca
        ca.IS_WINDOWS = True
        try:
            block = ca.shortcut_block()
        finally:
            ca.IS_WINDOWS = False
        ps1 = self.root / "block.ps1"
        ps1.write_text(block)
        lit = str(ps1).replace("'", "''")
        out = subprocess.run([sb.powershell(), "-NoProfile", "-Command",
                              f". '{lit}'; claude2 --resume; claude1"],
                             env=dict(os.environ, USERPROFILE=str(self.home)),
                             capture_output=True, text=True, timeout=120).stdout.splitlines()
        self.assertEqual(len(out), 2, out)
        self.assertTrue(out[0].startswith(f"DIR={self.home}\\.claude-account2|"))
        self.assertTrue(out[0].endswith("ARGS=--resume"))
        self.assertTrue(out[1].startswith("DIR=DEFAULT|"))

    @unittest.skipUnless(sb.powershell(), "PowerShell not installed")
    def test_profile_path_lookup_with_unicode_user(self):
        paths = self.ca.powershell_profiles()
        self.assertTrue(paths)
        self.assertTrue(str(paths[0]).endswith("Microsoft.PowerShell_profile.ps1"))


if __name__ == "__main__":
    unittest.main()
