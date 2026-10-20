"""Real-Windows checks: junctions, file links, PowerShell shortcuts, the GUI launcher and a
real new console window. Runs only on Windows (for example the GitHub Actions windows job).

Safe to run on your own PC: it uses a throwaway home folder, a fake `claude`, a temporary
PowerShell profile, and never changes your execution policy.
"""
import codecs
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox as sb  # noqa: E402

FOLDERS_FIRST = "Windows only"


def norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


@unittest.skipUnless(sb.IS_WIN, FOLDERS_FIRST)
class RealWindows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.home = sb.make_home()              # contains ' & ( ) $ on purpose
        cls.fake = sb.fake_claude(cls.root / "bin")
        ca = cls.ca = sb.load_cli(cls.home)
        cls.real_profiles = ca.powershell_profiles
        cls.profile = cls.root / "profile" / "Microsoft.PowerShell_profile.ps1"
        ca.powershell_profiles = lambda: [cls.profile]     # never touch your real profile
        ca.check_execution_policy = lambda: None            # never change your real policy
        cls.main = ca.MAIN_DIR
        (cls.main / "skills" / "s1").mkdir(parents=True)
        (cls.main / "settings.json").write_text('{"a": 1}')
        cls.setup_output = sb.quiet(ca.cmd_setup, 2)
        cls.a2 = ca.account_dir(2)
        cls.ps = shutil.which("powershell") or shutil.which("pwsh")

    @classmethod
    def tearDownClass(cls):
        sb.cleanup(cls.root)

    def run_ps(self, command, env=None, cwd=None):
        r = subprocess.run([self.ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                           env=env or os.environ.copy(), cwd=cwd, capture_output=True, text=True,
                           timeout=120)
        return [x.strip() for x in r.stdout.splitlines() if x.strip()], r.stderr

    def test_a_path_has_ampersand(self):
        self.assertIn("&", str(self.home))

    def test_b_folders_are_real_junctions(self):
        for name in self.ca.SHARED_FOLDERS:
            p = self.a2 / name
            with self.subTest(name):
                self.assertTrue(self.ca.is_link_or_junction(p), self.setup_output)
                if hasattr(os.path, "isjunction"):
                    self.assertTrue(os.path.isjunction(p) or p.is_symlink())
                self.assertTrue(self.ca.same_target(p, self.main / name))

    def test_c_files_linked(self):
        for name in self.ca.SHARED_FILES:
            with self.subTest(name):
                self.assertTrue(self.ca.same_target(self.a2 / name, self.main / name), self.setup_output)

    def test_d_sharing_works_both_ways(self):
        (self.main / "settings.json").write_text('{"a": 2}')
        self.assertIn("2", (self.a2 / "settings.json").read_text())
        (self.a2 / "projects" / "chat.jsonl").write_text("x")
        self.assertTrue((self.main / "projects" / "chat.jsonl").exists())

    def test_e_status_healthy(self):
        self.assertNotIn("NOT LINKED", sb.quiet(self.ca.cmd_status))

    def test_f_profile_shortcuts_in_real_powershell(self):
        self.assertTrue(self.profile.exists(), self.setup_output)
        lit = str(self.profile).replace("'", "''")
        env = dict(os.environ, USERPROFILE=str(self.home))
        env.pop("CLAUDE_CONFIG_DIR", None)
        lines, err = self.run_ps(f". '{lit}'; claude2 --resume; claude1", env=env)
        lines = [x for x in lines if x.startswith("DIR=")]
        self.assertEqual(len(lines), 2, err)
        self.assertTrue(norm(lines[0][4:].split("|")[0]) == norm(self.a2), lines[0])
        self.assertTrue(lines[0].endswith("ARGS=--resume"), lines[0])
        self.assertTrue(lines[1].startswith("DIR=DEFAULT|"), lines[1])

    def test_g_profile_encoding_preserved(self):
        p = self.root / "bom" / "profile.ps1"
        p.parent.mkdir()
        p.write_bytes(codecs.BOM_UTF8 + "# नमस्ते prompt\r\n".encode("utf-8"))
        self.ca.powershell_profiles = lambda: [p]
        try:
            sb.quiet(self.ca.write_shortcuts)
            sb.quiet(self.ca.write_shortcuts)
        finally:
            self.ca.powershell_profiles = lambda: [self.profile]
        raw = p.read_bytes()
        self.assertTrue(raw.startswith(codecs.BOM_UTF8))
        self.assertIn("नमस्ते", raw.decode("utf-8-sig"))
        self.assertNotIn(b"\r\r", raw)
        self.assertIn(b"\r\n", raw)

    def test_h_real_profile_path_lookup_read_only(self):
        paths = self.real_profiles()                 # only reads $PROFILE, writes nothing
        self.assertTrue(paths)
        self.assertTrue(all(str(x).endswith("_profile.ps1") for x in paths), paths)

    def gui(self):
        return sb.load_gui(self.home)

    def test_i_gui_launcher_command_in_real_powershell(self):
        g = self.gui()
        proj = self.home / sb.HOSTILE_PROJECT
        proj.mkdir(exist_ok=True)
        cmd = g.windows_command(2, str(proj), shutil.which("claude"))
        lines, err = self.run_ps(cmd, env=g.launch_env(2), cwd=str(proj))
        line = next((x for x in lines if x.startswith("DIR=")), "")
        self.assertEqual(norm(line[4:].split("|")[0]), norm(self.a2), err)
        self.assertEqual(norm(line.split("|PWD=")[1].split("|")[0]), norm(proj), err)

    def test_j_new_console_window_gets_right_account(self):
        g = self.gui()
        proj = self.home / sb.HOSTILE_PROJECT
        proj.mkdir(exist_ok=True)
        out = self.root / "console-out.txt"
        env = dict(g.launch_env(2), FAKE_CLAUDE_OUT=str(out))
        cmd = g.windows_command(2, str(proj), shutil.which("claude")) + "; exit"
        p = subprocess.Popen([self.ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                             cwd=str(proj), env=env,
                             creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10))
        p.wait(timeout=120)
        for _ in range(50):
            if out.exists():
                break
            time.sleep(0.1)
        text = out.read_text(encoding="utf-8")
        self.assertEqual(norm(text.split("|")[0][4:]), norm(self.a2), text)

    def test_z_undo_removes_links_and_keeps_account_1(self):
        answers = iter([True, False])
        self.ca.ask_yes = lambda q: next(answers)
        try:
            sb.quiet(self.ca.cmd_undo, 2)
        finally:
            self.ca.ask_yes = lambda q: True
        for name in self.ca.SHARED_FOLDERS:
            with self.subTest(name):
                self.assertFalse(self.ca.is_link_or_junction(self.a2 / name))
        self.assertTrue((self.main / "skills" / "s1").exists())
        self.assertIn('"a"', (self.main / "settings.json").read_text())


if __name__ == "__main__":
    unittest.main()
