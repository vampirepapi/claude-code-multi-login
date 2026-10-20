"""Test sandbox helpers.

Every test runs against a throwaway home folder and a fake `claude` command, so the
tests never touch your real ~/.claude, PowerShell profile, shell config or login.
"""
import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

# Hostile on purpose: quotes, $, backtick, &, ;, non-ASCII and emoji catch quoting bugs.
HOSTILE_HOME = "O'Brien & R&D (x) $HOME" if IS_WIN else "O'Brien $HOME `tick` & ; रवि 🚀"
HOSTILE_PROJECT = "proj & (q) $x" if IS_WIN else 'proj "q" $x'

FAKE_CLAUDE = r'''import os, sys
out = ("DIR=" + os.environ.get("CLAUDE_CONFIG_DIR", "DEFAULT") + "|PWD=" + os.getcwd()
       + "|ARGS=" + " ".join(sys.argv[1:]))
target = os.environ.get("FAKE_CLAUDE_OUT")
if target:
    with open(target, "a", encoding="utf-8") as fh:
        fh.write(out + "\n")
else:
    print(out)
'''


def make_home(name=HOSTILE_HOME):
    """Create <tmp>/<name> and return (root, home). Paths are fully resolved."""
    root = Path(tempfile.mkdtemp(prefix="cca-test-")).resolve()
    home = root / name
    home.mkdir(parents=True)
    return root, home


def set_home(home: Path):
    os.environ["HOME"] = str(home)
    os.environ["USERPROFILE"] = str(home)
    os.environ["SHELL"] = "/bin/zsh"
    os.environ.pop("CLAUDE_CONFIG_DIR", None)


def load_cli(home: Path, alias="claude_accounts_under_test"):
    """Import a fresh copy of claude_accounts.py whose HOME is the sandbox."""
    set_home(home)
    spec = importlib.util.spec_from_file_location(alias, REPO / "claude_accounts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ask_yes = lambda q: True          # tests answer "yes" unless they say otherwise
    return mod


def load_gui(home: Path):
    """Import a fresh claude_accounts_gui (and its claude_accounts) for the sandbox."""
    set_home(home)
    for name in ("claude_accounts", "claude_accounts_gui"):
        sys.modules.pop(name, None)
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    import claude_accounts_gui  # noqa: PLC0415
    return claude_accounts_gui


def fake_claude(bindir: Path) -> Path:
    """Put a fake `claude` first on PATH. It reports which account/folder it got."""
    bindir.mkdir(parents=True, exist_ok=True)
    script = bindir / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    if IS_WIN:
        exe = bindir / "claude.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        exe = bindir / "claude"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        exe.chmod(0o755)
    os.environ["PATH"] = str(bindir) + os.pathsep + os.environ.get("PATH", "")
    return exe


def quiet(fn, *args):
    """Run fn, return what it printed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


def powershell():
    return shutil.which("pwsh") or (shutil.which("powershell") if IS_WIN else None)


def cleanup(root: Path):
    shutil.rmtree(root, ignore_errors=True)
