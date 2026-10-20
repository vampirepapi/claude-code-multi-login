"""Tests for claude_accounts_gui.py. Needs Tkinter and a display (on Linux CI: xvfb-run).

Set CCA_SKIP_GUI=1 to skip. The Windows launcher is also checked for real in test_windows.py.
"""
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _sandbox as sb  # noqa: E402


def gui_unavailable():
    if sb.IS_WIN:
        return "Windows launcher is covered by test_windows.py"
    if os.environ.get("CCA_SKIP_GUI"):
        return "CCA_SKIP_GUI is set"
    try:
        import tkinter  # noqa: F401, PLC0415
    except ImportError:
        return "Tkinter not installed"
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return "no display (use xvfb-run)"
    return None


@unittest.skipIf(gui_unavailable(), gui_unavailable())
class GuiScenario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root_dir, cls.home = sb.make_home()
        main = cls.home / ".claude"
        (main / "skills" / "s1").mkdir(parents=True)
        (cls.home / ".claude.json").write_text('{"mcpServers":{"github":{"command":"gh"}}}')
        sb.fake_claude(cls.root_dir / "bin")
        g = cls.g = sb.load_gui(cls.home)
        cls.ca = g.ca
        cls.asked, cls.errors, cls.answer = [], [], {"v": True}

        def fake_ask(title, q, **k):
            cls.asked.append((q, threading.current_thread() is threading.main_thread()))
            return cls.answer["v"]
        g.messagebox.askyesno = fake_ask
        g.messagebox.showerror = lambda t, m, **k: cls.errors.append(m)
        g.messagebox.showinfo = lambda *a, **k: None
        cls.tk = g.tk.Tk()
        cls.app = g.App(cls.tk)
        cls.tk.update()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.tk.destroy()
        except Exception:  # noqa: BLE001
            pass
        sb.cleanup(cls.root_dir)

    # helpers
    def wait(self, timeout=60):
        t0 = time.time()
        self.tk.update()
        while self.app.working and time.time() - t0 < timeout:
            self.tk.update()
            time.sleep(0.01)
        self.tk.update()

    def row(self, n):
        return self.app.tree.item(str(n))["values"]

    def log(self):
        return self.app.text.get("1.0", "end")

    def check(self, cond, msg):
        with self.subTest(msg):
            self.assertTrue(cond, msg)

    def launch(self, n, windows):
        """Click Open terminal with Popen captured; return (args, kwargs)."""
        calls = []

        class FakePopen:
            def __init__(s, args, **kw):
                calls.append((args, kw))
                s.returncode = 0

            def poll(s):
                return 0
        g, real = self.g, self.g.subprocess
        self.ca.IS_WINDOWS, self.ca.IS_MAC = windows, not windows
        g.subprocess = types.SimpleNamespace(Popen=FakePopen, PIPE=subprocess.PIPE)
        try:
            self.app.tree.selection_set(str(n))
            self.tk.update()
            self.app.open_terminal()
        finally:
            g.subprocess, self.ca.IS_WINDOWS, self.ca.IS_MAC = real, False, sb.IS_MAC
        return calls[-1] if calls else (None, None)

    def test_full_scenario(self):
        app, ca, g = self.app, self.ca, self.g
        total = len(ca.shared_items())
        self.check(len(app.tree.get_children()) == 1 and "Only your main account" in self.log(),
                   "fresh start: one account and a clear next step")

        app.add_account()
        self.wait()
        self.check(self.row(2)[2] == f"{total} of {total} linked", "Add account links everything")
        self.check(self.asked and all(main for _, main in self.asked),
                   "questions from the worker are asked on the UI thread")
        self.check("Next: with claude2 selected" in self.log(), "next-step tip shown")
        self.check("pending" in app.tree.item("2")["tags"], "not-logged-in account is marked")

        ticks = []

        def tick():
            ticks.append(time.time())
            if app.working:
                self.tk.after(50, tick)
        app.run("Slow job", lambda: time.sleep(2))
        self.tk.after(0, tick)
        self.wait()
        gap = max(b - a for a, b in zip(ticks, ticks[1:]))
        self.check(gap < 0.5, f"window stays responsive during slow work (pause {gap:.2f}s)")

        app.run("Busy", lambda: time.sleep(0.5))
        app.add_account()
        self.wait()
        self.check("3" not in app.tree.get_children(), "no second action while one is running")

        app.tree.selection_set("2")
        self.tk.update()
        app.sync_mcp()
        self.wait()
        self.check(self.errors and "log in first" in self.errors[-1], "Copy MCP before login: clear error")
        (self.home / ".claude-account2" / ".claude.json").write_text('{"theme":"dark"}')
        app.sync_mcp()
        self.wait()
        self.check("Copied 1 MCP server" in self.log() and self.row(2)[3] == "Set up",
                   "Copy MCP after login works")

        settings = self.home / ".claude-account2" / "settings.json"
        settings.unlink()
        settings.write_text("{}")
        app.refresh()
        self.check("needs repair" in self.row(2)[2], "broken link shown as needs repair")
        proj = self.home / sb.HOSTILE_PROJECT
        proj.mkdir()
        app.project_dir.set(str(proj))
        self.answer["v"] = False
        args, _ = self.launch(2, windows=False)
        self.check(args is None and "need repair" in self.asked[-1][0],
                   "opening a broken account warns first")
        self.answer["v"] = True
        app.repair()
        self.wait()
        self.check(self.row(2)[2] == f"{total} of {total} linked", "Repair links fixes it")

        clean = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        ps = sb.powershell()
        if ps:
            args, kw = self.launch(2, windows=True)
            cmd = args[args.index("-Command") + 1]
            out = subprocess.run([ps, "-NoProfile", "-Command", cmd], env=kw["env"], cwd=kw["cwd"],
                                 capture_output=True, text=True, timeout=120).stdout
            self.check(f"DIR={self.home}/.claude-account2|PWD={proj}|" in out,
                       "Windows launcher in real PowerShell: right account and folder")
            self.check("& '" in cmd, "Windows launcher calls the resolved claude program")
            args, kw = self.launch(1, windows=True)
            cmd = args[args.index("-Command") + 1]
            out = subprocess.run([ps, "-NoProfile", "-Command", cmd],
                                 env=dict(kw["env"], CLAUDE_CONFIG_DIR="/leaked"), cwd=kw["cwd"],
                                 capture_output=True, text=True, timeout=120).stdout
            self.check(out.startswith("DIR=DEFAULT|"), "Windows launcher claude1 ignores a leaked variable")

        args, _ = self.launch(2, windows=False)
        m = re.search(r'do script "((?:[^"\\]|\\.)*)"', args[2])
        shell_cmd = re.sub(r"\\(.)", r"\1", m.group(1))
        self.check(shell_cmd == g.mac_shell_command(2, str(proj)), "Mac AppleScript escaping round-trips")
        if shutil.which("zsh"):
            out = subprocess.run(["zsh", "-c", shell_cmd], capture_output=True, text=True,
                                 env=clean, timeout=60).stdout
            self.check(f"DIR={self.home}/.claude-account2|PWD={proj}|" in out,
                       "Mac launcher in real zsh: right account and folder")
            args, _ = self.launch(1, windows=False)
            s1 = re.sub(r"\\(.)", r"\1", re.search(r'do script "((?:[^"\\]|\\.)*)"', args[2]).group(1))
            out = subprocess.run(["zsh", "-c", s1], capture_output=True, text=True,
                                 env=dict(clean, CLAUDE_CONFIG_DIR="/leaked"), timeout=60).stdout
            self.check(out.startswith("DIR=DEFAULT|"), "Mac launcher claude1 ignores a leaked variable")

        class Denied:
            returncode = 1

            def poll(self):
                return 1

            class stderr:
                read = staticmethod(lambda: "Not authorized to send Apple events to Terminal. (-1743)")
        app._check_osascript(Denied())
        self.check("Privacy & Security > Automation" in self.errors[-1], "Mac permission denial explained")
        app.project_dir.set(str(self.home / "missing"))
        app.open_terminal()
        self.check("doesn't exist" in self.errors[-1], "missing folder: clear error")

        app.add_account()
        self.wait()
        app.tree.selection_set("3")
        self.tk.update()
        app.undo()
        self.wait()
        self.check("3" not in app.tree.get_children() and (self.home / ".claude" / "skills" / "s1").exists(),
                   "Undo setup removes claude3, account 1 safe")
        self.check("<Key-Return>" in app.tree.bind(), "Enter key opens a terminal")
        app.working, self.answer["v"] = True, False
        app.on_close()
        self.check(self.tk.winfo_exists() and "Still working" in self.asked[-1][0],
                   "closing mid-operation asks first")
        app.working, self.answer["v"] = False, True


if __name__ == "__main__":
    unittest.main()
