#!/usr/bin/env python3
"""
claude_accounts_gui.py - a window for claude_accounts.py (Windows and macOS).

Keep this file in the SAME folder as claude_accounts.py, then run:
  Windows:  python claude_accounts_gui.py
  Mac:      python3 claude_accounts_gui.py

All the setup logic lives in claude_accounts.py; this file only adds the window
and the "Open terminal" launcher. Requires Python 3.8+ with Tkinter.
"""

import io
import os
import queue
import shlex
import shutil
import subprocess
import sys
import threading
import traceback
from contextlib import redirect_stdout
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
    import tkinter.font as tkfont
except ImportError:
    sys.exit("Tkinter is missing.\n"
             "  Mac (Homebrew Python): brew install python-tk\n"
             "  Windows: re-run the python.org installer and tick 'tcl/tk and IDLE'.")

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import claude_accounts as ca
except ImportError:
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror("Claude Code accounts",
                         "claude_accounts.py was not found.\n\n"
                         "Put it in the same folder as this file and try again.")
    sys.exit(1)

APP_TITLE = "Claude Code accounts"


# ------------------------------------------------------------ terminal launch

def ps_quote(s: str) -> str:
    """Single-quoted PowerShell string ('' escapes a quote)."""
    return "'" + s.replace("'", "''") + "'"


def applescript_quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def windows_command(n: int, cwd: str, claude_path=None) -> str:
    env = ("Remove-Item Env:\\CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue" if n == 1
           else f"$env:CLAUDE_CONFIG_DIR = {ps_quote(str(ca.account_dir(n)))}")
    run = f"& {ps_quote(claude_path)}" if claude_path else "claude"
    return (f"{env}; Set-Location -LiteralPath {ps_quote(cwd)}; "
            f"$host.UI.RawUI.WindowTitle = 'claude{n}'; {run}")


def mac_shell_command(n: int, cwd: str) -> str:
    env = ("unset CLAUDE_CONFIG_DIR" if n == 1
           else f"export CLAUDE_CONFIG_DIR={shlex.quote(str(ca.account_dir(n)))}")
    return f"cd {shlex.quote(cwd)} && {env} && command claude"


def mac_applescript(n: int, cwd: str) -> str:
    return ('tell application "Terminal"\n'
            '  activate\n'
            f'  do script {applescript_quote(mac_shell_command(n, cwd))}\n'
            'end tell')


def launch_env(n: int) -> dict:
    env = os.environ.copy()
    if n == 1:
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = str(ca.account_dir(n))
    return env


# ------------------------------------------------------------ the window

class QueueWriter(io.TextIOBase):
    """stdout replacement for the worker thread: hands text to the UI thread."""
    def __init__(self, q):
        self.q = q

    def write(self, s):
        self.q.put(("log", s))
        return len(s)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_TITLE)
        root.geometry("860x560")
        root.minsize(700, 460)
        ca.ask_yes = self.ask_yes              # questions become yes/no popups
        sys.argv[0] = ca.__file__              # printed hints name the right script
        self.project_dir = tk.StringVar(value=str(ca.HOME))
        self.status = tk.StringVar(value="Ready")
        self.buttons = {}
        self.working = False
        self._q = None
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build()
        self.refresh()
        self._intro()

    # ---------- layout
    def _build(self):
        base = tkfont.nametofont("TkDefaultFont")
        size = base.cget("size")
        title_font = base.copy()
        title_font.configure(size=(size - 6) if size < 0 else (size + 5), weight="bold")

        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=APP_TITLE, font=title_font).pack(anchor="w")
        ttk.Label(outer, text="Each account signs in separately and shares skills, settings "
                              "and chat history with account 1.").pack(anchor="w", pady=(2, 12))

        table = ttk.Frame(outer)
        table.pack(fill="x")
        cols = ("command", "folder", "shared", "login")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=5,
                                 selectmode="browse")
        for col, text, width in (("command", "Command", 90), ("folder", "Folder", 260),
                                 ("shared", "Shared items", 220), ("login", "Login", 150)):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=width, anchor="w", stretch=(col == "folder"))
        self.tree.tag_configure("problem", foreground="#b42318")
        self.tree.tag_configure("pending", foreground="#8a6100")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="x", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())
        self.tree.bind("<Double-1>", lambda _e: self.open_terminal())
        self.tree.bind("<Return>", lambda _e: self.open_terminal())

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(10, 0))
        for key, text, cmd, side in (
                ("open", "Open terminal", self.open_terminal, "left"),
                ("repair", "Repair links", self.repair, "left"),
                ("mcp", "Copy MCP servers", self.sync_mcp, "left"),
                ("undo", "Undo setup", self.undo, "left"),
                ("refresh", "Refresh", self.refresh, "right"),
                ("add", "Add account", self.add_account, "right")):
            b = ttk.Button(actions, text=text, command=cmd)
            b.pack(side=side, padx=(0, 6) if side == "left" else (6, 0))
            self.buttons[key] = b

        folder = ttk.Frame(outer)
        folder.pack(fill="x", pady=(12, 0))
        ttk.Label(folder, text="Open terminals in").pack(side="left")
        ttk.Entry(folder, textvariable=self.project_dir).pack(side="left", fill="x",
                                                              expand=True, padx=8)
        ttk.Button(folder, text="Choose folder…", command=self.choose_folder).pack(side="left")

        logbox = ttk.LabelFrame(outer, text="Activity", padding=(8, 6))
        logbox.pack(fill="both", expand=True, pady=(14, 0))
        self.text = tk.Text(logbox, height=10, wrap="word", relief="flat",
                            font=tkfont.nametofont("TkFixedFont"), state="disabled",
                            borderwidth=0, highlightthickness=0)
        tscroll = ttk.Scrollbar(logbox, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=tscroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        tscroll.pack(side="left", fill="y")
        heading_font = tkfont.nametofont("TkFixedFont").copy()
        heading_font.configure(weight="bold")
        self.text.tag_configure("heading", font=heading_font, spacing1=6)

        ttk.Label(outer, textvariable=self.status).pack(anchor="w", pady=(8, 0))

    # ---------- helpers
    def ask_yes(self, question: str) -> bool:
        if threading.current_thread() is threading.main_thread() or self._q is None:
            return messagebox.askyesno(APP_TITLE, question, parent=self.root)
        done, box = threading.Event(), {}
        self._q.put(("ask", question, done, box))
        done.wait()
        return box["answer"]

    def log(self, s: str, tag=None):
        self.text.configure(state="normal")
        self.text.insert("end", s, tag or ())
        self.text.see("end")
        self.text.configure(state="disabled")
        self.root.update_idletasks()

    def selected(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _update_buttons(self):
        n = self.selected()
        for key in ("repair", "mcp", "undo"):
            self.buttons[key].state(["!disabled"] if n and n >= 2 else ["disabled"])
        self.buttons["open"].state(["!disabled"] if n else ["disabled"])

    def _busy(self, on: bool, label=""):
        self.working = on
        self.root.configure(cursor="watch" if on else "")
        for b in self.buttons.values():
            b.state(["disabled"] if on else ["!disabled"])
        self.status.set(f"{label}…" if on else "Ready")
        if not on:
            self._update_buttons()
        self.root.update_idletasks()

    def run(self, label: str, fn, then=None):
        """Run a claude_accounts command on a worker thread so the window never freezes.
        Its printed output streams into the Activity log; its questions become popups."""
        if self.working:
            return
        self.log(f"{label}\n", "heading")
        self._busy(True, label)
        q = self._q = queue.Queue()

        def worker():
            try:
                with redirect_stdout(QueueWriter(q)):
                    fn()
                q.put(("done", None))
            except SystemExit as e:             # claude_accounts uses sys.exit for errors
                q.put(("exit", e.code))
            except Exception as e:  # noqa: BLE001 - shown to the user, never swallowed
                q.put(("error", f"{type(e).__name__}: {e}", traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(30, lambda: self._pump(q, label, then))

    def _pump(self, q, label, then):
        while True:
            try:
                msg = q.get_nowait()
            except queue.Empty:
                self.root.after(30, lambda: self._pump(q, label, then))
                return
            kind = msg[0]
            if kind == "log":
                self.log(msg[1])
            elif kind == "ask":
                _, question, done, box = msg
                box["answer"] = messagebox.askyesno(APP_TITLE, question, parent=self.root)
                done.set()
            else:
                if kind == "exit" and msg[1] not in (None, 0):
                    self.log(f"{msg[1]}\n")
                    messagebox.showerror(label, str(msg[1]), parent=self.root)
                elif kind == "error":
                    self.log(msg[2])
                    messagebox.showerror(label, msg[1], parent=self.root)
                self._q = None
                self._busy(False)
                self.refresh()
                if then:
                    then()
                return

    def on_close(self):
        if self.working and not messagebox.askyesno(
                APP_TITLE, "Still working. Quit anyway? You can run Repair links afterwards.",
                parent=self.root):
            return
        self.root.destroy()

    # ---------- table
    def describe(self, n: int):
        d = ca.account_dir(n)
        home = str(ca.HOME)
        folder = "~" + str(d)[len(home):] if str(d).startswith(home) else str(d)
        if n == 1:
            ok = d.exists()
            shared = "Main account (the source)" if ok else "Folder missing"
            login = "Set up" if ok else "Not yet: run claude once"
            return (f"claude{n}", folder, shared, login), ("" if ok else "problem")
        items = ca.shared_items()
        linked = sum(ca.same_target(d / x, ca.MAIN_DIR / x) for x, _ in items)
        total = len(items)
        shared = f"{linked} of {total} linked" + ("" if linked == total else ", needs repair")
        logged_in = (d / ".claude.json").exists()
        login = "Set up" if logged_in else "Not yet: open terminal to log in"
        tag = "problem" if linked < total else ("" if logged_in else "pending")
        return (f"claude{n}", folder, shared, login), tag

    def needs_repair(self, n: int) -> bool:
        return n >= 2 and self.describe(n)[1] == "problem"

    def refresh(self):
        keep = self.selected()
        self.tree.delete(*self.tree.get_children())
        nums = ca.existing_accounts()
        for n in nums:
            values, tag = self.describe(n)
            self.tree.insert("", "end", iid=str(n), values=values, tags=(tag,) if tag else ())
        pick = keep if keep in nums else (nums[1] if len(nums) > 1 else nums[0])
        self.tree.selection_set(str(pick))
        self.tree.focus(str(pick))
        self._update_buttons()

    def _intro(self):
        if not ca.MAIN_DIR.exists():
            self.log("Account 1 isn't set up yet. Run claude once in a terminal, log in, "
                     "then click Refresh.\n")
        elif len(ca.existing_accounts()) == 1:
            self.log("Only your main account exists. Click Add account to create claude2.\n")
        else:
            self.log("Select an account and click Open terminal (or double-click it) to "
                     "start Claude Code with that account.\n")

    # ---------- actions
    def add_account(self):
        if self.working:
            return
        n = max(ca.existing_accounts()) + 1
        existed = ca.account_dir(n).exists()

        def after():
            if ca.account_dir(n).exists() and not existed:
                self.tree.selection_set(str(n))
                self.log(f"\nNext: with claude{n} selected, click Open terminal and log in "
                         f"with that account. Then click Copy MCP servers.\n")
        self.run(f"Add account claude{n}", lambda: ca.cmd_setup(n), then=after)

    def repair(self):
        n = self.selected()
        if n and n >= 2:
            self.run(f"Repair links for claude{n}", lambda: ca.cmd_setup(n))

    def sync_mcp(self):
        n = self.selected()
        if n and n >= 2:
            self.run(f"Copy MCP servers to claude{n}", lambda: ca.cmd_sync_mcp(n))

    def undo(self):
        n = self.selected()
        if n and n >= 2:
            self.run(f"Undo setup for claude{n}", lambda: ca.cmd_undo(n))

    def choose_folder(self):
        path = filedialog.askdirectory(initialdir=self.project_dir.get() or str(ca.HOME),
                                       parent=self.root, title="Folder to open terminals in")
        if path:
            self.project_dir.set(str(Path(path)))

    def open_terminal(self):
        n = self.selected()
        if not n or self.working:
            return
        if self.needs_repair(n) and not messagebox.askyesno(
                "Open terminal", f"claude{n}'s shared links need repair, so it may not have "
                "all of account 1's skills and settings.\n\nOpen it anyway?", parent=self.root):
            return
        cwd = self.project_dir.get().strip()
        if not cwd or not Path(cwd).is_dir():
            messagebox.showerror("Open terminal", f"This folder doesn't exist:\n{cwd}\n\n"
                                 "Choose another folder.", parent=self.root)
            return
        cwd = str(Path(cwd).resolve())
        try:
            if ca.IS_WINDOWS:
                exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
                cmd = windows_command(n, cwd, shutil.which("claude"))
                subprocess.Popen([exe, "-NoExit", "-Command", cmd],
                                 cwd=cwd, env=launch_env(n),
                                 creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10))
            elif ca.IS_MAC:
                p = subprocess.Popen(["osascript", "-e", mac_applescript(n, cwd)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.root.after(500, lambda: self._check_osascript(p))
            else:
                messagebox.showinfo("Open terminal", "Open terminal works on Windows and macOS.",
                                    parent=self.root)
                return
        except OSError as e:
            messagebox.showerror("Open terminal", f"Couldn't open a terminal: {e}",
                                 parent=self.root)
            return
        self.log(f"Opened a terminal for claude{n} in {cwd}\n")

    def _check_osascript(self, p):
        if p.poll() is None:
            self.root.after(500, lambda: self._check_osascript(p))
            return
        if p.returncode != 0:
            err = (p.stderr.read() or "").strip()
            msg = ("macOS blocked this app from controlling Terminal. Allow it in System "
                   "Settings > Privacy & Security > Automation, then try again."
                   if "-1743" in err else f"Couldn't open Terminal: {err}")
            self.log(msg + "\n")
            messagebox.showerror("Open terminal", msg, parent=self.root)


def main():
    if ca.IS_WINDOWS:
        try:  # sharp text on high-DPI screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
