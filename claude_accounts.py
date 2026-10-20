#!/usr/bin/env python3
"""
claude_accounts.py - run several Claude Code accounts on one computer (Windows or macOS).

Account 1 uses the normal folder (~/.claude). Account N uses ~/.claude-accountN.
Each extra account keeps its OWN login, but links to account 1's skills, agents,
commands, plugins, hooks, settings, global CLAUDE.md and conversation history.

Commands:
  python claude_accounts.py setup [N]     Create/repair account N (default: next free number)
  python claude_accounts.py status        Show all accounts and whether links are healthy
  python claude_accounts.py sync-mcp N    Copy account 1's user-level MCP servers to account N
  python claude_accounts.py undo N        Remove account N's links and restore its backups

Never linked or copied: .credentials.json and .claude.json (they hold the login).
Requires Python 3.8+. No admin needed (on Windows, file links fall back to hard links).
"""

import argparse
import base64
import codecs
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---- What gets shared with account 1 (edit these lists if you like) ----
SHARED_FOLDERS = ["skills", "agents", "commands", "plugins", "hooks", "rules",
                  "output-styles", "projects", "todos", "plans", "file-history",
                  "agent-memory"]
SHARED_FILES = ["CLAUDE.md", "settings.json", "history.jsonl"]
OPTIONAL_FILES = ["keybindings.json"]          # linked once account 1 has one
# Chat/work records from account N are merged into account 1 before linking (never lost)
MERGE_FOLDERS = {"projects", "todos", "plans", "file-history", "agent-memory"}
# Never shared (per-account): .credentials.json, .claude.json, remote-settings.json,
# policy-limits.json, statsig, ide, sessions, session-env, shell-snapshots, logs, backups

__version__ = "1.0.0"

HOME = Path.home()
MAIN_DIR = HOME / ".claude"
IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
BLOCK_START = "# >>> claude-accounts >>>"
BLOCK_END = "# <<< claude-accounts <<<"


# ---------------------------------------------------------------- helpers

def shared_items():
    """(name, is_folder) for everything linked to account 1 right now."""
    items = [(x, True) for x in SHARED_FOLDERS] + [(x, False) for x in SHARED_FILES]
    return items + [(x, False) for x in OPTIONAL_FILES if (MAIN_DIR / x).exists()]


def all_item_names():
    return SHARED_FOLDERS + SHARED_FILES + OPTIONAL_FILES


def account_dir(n: int) -> Path:
    return MAIN_DIR if n == 1 else HOME / f".claude-account{n}"


def existing_accounts():
    nums = [1]
    for p in HOME.glob(".claude-account*"):
        m = re.fullmatch(r"\.claude-account(\d+)", p.name)
        if m and p.is_dir():
            nums.append(int(m.group(1)))
    return sorted(set(nums))


def same_target(link: Path, target: Path) -> bool:
    try:
        if not link.exists():
            return False
        if os.path.normcase(os.path.realpath(link)) == os.path.normcase(os.path.realpath(target)):
            return True
        return link.is_file() and target.is_file() and os.path.samefile(link, target)  # hard link
    except OSError:
        return False


def is_link_or_junction(p: Path) -> bool:
    if p.is_symlink():
        return True
    if IS_WINDOWS:
        try:
            os.readlink(p)  # works for junctions on Python 3.8+
            return True
        except (OSError, ValueError):
            return False
    return False


def remove_link(p: Path):
    """Remove a symlink/junction/hard link WITHOUT touching what it points to."""
    if IS_WINDOWS and p.is_dir() and is_link_or_junction(p):
        os.rmdir(p)          # removes the junction only
    else:
        os.unlink(p)


def backup_name(p: Path) -> Path:
    stamp = time.strftime("%Y%m%d%H%M%S")
    dest, i = p.with_name(f"{p.name}.bak-{stamp}"), 1
    while dest.exists() or dest.is_symlink():          # never overwrite an older backup
        dest, i = p.with_name(f"{p.name}.bak-{stamp}-{i}"), i + 1
    return dest


def backup(p: Path) -> Path:
    dest = backup_name(p)
    p.rename(dest)
    return dest


def ask_yes(question: str) -> bool:
    try:
        return input(f"{question} [y/N]: ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


# ---------------------------------------------------------------- linking

def link_folder(src: Path, dst: Path) -> str:
    if IS_WINDOWS:
        try:
            import _winapi                        # ships with CPython on Windows
            _winapi.CreateJunction(str(src), str(dst))
            return "junction"
        except (ImportError, AttributeError):
            pass
        r = subprocess.run(f'cmd /c mklink /J "{dst}" "{src}"', capture_output=True,
                           text=True, check=False)   # quoted: safe with '&' in paths
        if r.returncode != 0:
            raise OSError(r.stderr.strip() or r.stdout.strip())
        return "junction"
    os.symlink(src, dst, target_is_directory=True)
    return "symlink"


def link_file(src: Path, dst: Path) -> str:
    try:
        os.symlink(src, dst)
        return "symlink"
    except OSError:
        if not IS_WINDOWS:
            raise
    os.link(src, dst)   # Windows without admin/Developer Mode
    return "hard link"


def ensure_source(name: str, is_folder: bool) -> Path:
    src = MAIN_DIR / name
    if not src.exists():
        if is_folder:
            src.mkdir(parents=True)
        elif name.endswith(".json"):
            src.write_text("{}\n", encoding="utf-8")
        else:
            src.touch()
    return src


def merge_missing(src: Path, dst: Path) -> int:
    """Copy entries that exist only in dst (account N) into src (account 1)."""
    copied = 0
    for entry in dst.iterdir():
        target = src / entry.name
        if entry.is_symlink():
            continue
        if not target.exists():
            if entry.is_dir():
                shutil.copytree(entry, target, symlinks=True)
            else:
                shutil.copy2(entry, target)
            copied += 1
        elif entry.is_dir() and target.is_dir():
            copied += merge_missing(target, entry)
    return copied


def link_item(n: int, name: str, is_folder: bool):
    src = ensure_source(name, is_folder)
    dst = account_dir(n) / name
    if same_target(dst, src):
        print(f"  ok        {name}")
        return
    if is_link_or_junction(dst) and not dst.exists():   # broken link
        remove_link(dst)
    elif dst.exists() or dst.is_symlink():
        if name in MERGE_FOLDERS and dst.is_dir() and not is_link_or_junction(dst):
            merged = merge_missing(src, dst)
            if merged:
                print(f"  merged    {merged} item(s) of account {n}'s {name} into account 1")
        print(f"  backup    {name} -> {backup(dst).name}")
    kind = link_folder(src, dst) if is_folder else link_file(src, dst)
    print(f"  linked    {name} ({kind})")
    if kind == "hard link":
        print("            note: if this stops syncing, run 'setup' again to repair it")


# ---------------------------------------------------------------- shortcuts

def powershell_profiles():
    paths = []
    for exe in ("powershell", "pwsh"):
        if shutil.which(exe):
            r = subprocess.run([exe, "-NoProfile", "-Command",
                                "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($PROFILE))"],
                               capture_output=True, check=False)
            try:
                path = base64.b64decode(r.stdout.strip()).decode("utf-8")
            except ValueError:
                continue
            if r.returncode == 0 and path:
                paths.append(Path(path))
    return paths


def unix_profile() -> Path:
    shell = os.environ.get("SHELL", "")
    if shell.endswith("bash"):
        return HOME / (".bash_profile" if IS_MAC else ".bashrc")
    return HOME / ".zshrc"


def shortcut_block() -> str:
    lines = [BLOCK_START, "# Managed by claude_accounts.py - re-run the script instead of editing"]
    for n in existing_accounts():
        if IS_WINDOWS:
            if n == 1:
                lines.append("function claude1 { Remove-Item Env:\\CLAUDE_CONFIG_DIR "
                             "-ErrorAction SilentlyContinue; claude @args }")
            else:
                lines.append(f'function claude{n} {{ $env:CLAUDE_CONFIG_DIR = '
                             f'"$env:USERPROFILE\\.claude-account{n}"; claude @args }}')
        else:
            if n == 1:
                lines.append("alias claude1='unset CLAUDE_CONFIG_DIR; command claude'")
            else:
                lines.append(f"alias claude{n}='CLAUDE_CONFIG_DIR=\"$HOME/.claude-account{n}\" "
                             f"command claude'")
    lines.append(BLOCK_END)
    return "\n".join(lines) + "\n"


def read_text_any(p: Path):
    """Return (text, encoding) so the file can be written back in its original encoding."""
    raw = p.read_bytes()
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8", "replace"), "utf-8-sig"
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16"), "utf-16"
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"     # byte-for-byte round trip


def write_shortcuts():
    targets = powershell_profiles() if IS_WINDOWS else [unix_profile()]
    if not targets:
        print("  Could not find a PowerShell profile. Add the shortcuts manually.")
        return
    block = shortcut_block()
    pattern = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END) + r"\n?", re.S)
    for prof in targets:
        prof.parent.mkdir(parents=True, exist_ok=True)
        text, enc = read_text_any(prof) if prof.exists() else ("", "utf-8")
        crlf = ("\r\n" in text) if text else IS_WINDOWS     # keep the file's own line endings
        text = text.replace("\r\n", "\n")
        new = pattern.sub(lambda _m: block, text) if BLOCK_START in text else \
            text + ("" if text.endswith("\n") or not text else "\n") + "\n" + block
        if new != text:
            with open(prof, "w", encoding=enc, newline="\r\n" if crlf else "\n") as fh:
                fh.write(new)
            print(f"  updated   {prof}")
        else:
            print(f"  ok        {prof}")


def check_execution_policy():
    if not IS_WINDOWS or not shutil.which("powershell"):
        return
    def policy():
        r = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-ExecutionPolicy"],
                           capture_output=True, text=True, check=False)
        return r.stdout.strip()
    blocked = ("Restricted", "AllSigned", "Undefined")
    current = policy()
    if current in blocked:
        print(f"\nPowerShell's execution policy is '{current}', so the shortcuts won't load.")
        if ask_yes("Set it to RemoteSigned for your user only?"):
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force"],
                           capture_output=True, check=False)
            if policy() in blocked:
                print("  Your organization's Group Policy blocks this, so the claude1/claude2\n"
                      "  shortcuts can't load. The GUI's Open terminal button still works.")
            else:
                print("  done")
        else:
            print("  Skipped. Run later: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned")


# ---------------------------------------------------------------- commands

def cmd_setup(n):
    if n is None:
        n = max(existing_accounts()) + 1
    if n < 2:
        sys.exit("Account 1 is your main account; pick 2 or higher.")
    if not shutil.which("claude"):
        print("Warning: 'claude' was not found on PATH. Install Claude Code first.")
    if not MAIN_DIR.exists():
        sys.exit(f"{MAIN_DIR} not found. Run 'claude' once and log in with account 1 first.")
    if not ask_yes(f"Close all Claude Code sessions first. Set up account {n} now?"):
        return
    dst = account_dir(n)
    dst.mkdir(exist_ok=True)
    print(f"\nLinking {dst} to {MAIN_DIR}:")
    for name, is_folder in shared_items():
        link_item(n, name, is_folder)
    print("\nShortcuts:")
    write_shortcuts()
    check_execution_policy()
    reload = "open a new PowerShell window" if IS_WINDOWS else f"run: source {unix_profile()}"
    print(f"""
Done. Next steps:
  1. {reload}
  2. Run  claude{n}  and log in with that account (first time only)
  3. Type /status to confirm the email
  4. Run  {'python' if IS_WINDOWS else 'python3'} {Path(sys.argv[0]).name} sync-mcp {n}  to copy your MCP servers""")


def cmd_status():
    for n in existing_accounts():
        d = account_dir(n)
        print(f"\nclaude{n}  ->  {d}")
        if n == 1:
            print("  main account (source of shared items)")
            continue
        for name, _ in shared_items():
            src, dst = MAIN_DIR / name, d / name
            state = "linked" if same_target(dst, src) else \
                ("NOT LINKED (run setup to repair)" if dst.exists() else "missing (run setup)")
            print(f"  {name:<17} {state}")
        logged_in = (d / ".claude.json").exists()
        print(f"  {'login':<17} {'set up' if logged_in else 'not yet - run claude' + str(n)}")


def cmd_sync_mcp(n):
    src_cfg, dst_cfg = HOME / ".claude.json", account_dir(n) / ".claude.json"
    if not src_cfg.exists():
        sys.exit(f"{src_cfg} not found - account 1 may have no MCP servers.")
    if not dst_cfg.exists():
        sys.exit(f"{dst_cfg} not found. Run claude{n} and log in first, then try again.")
    if not ask_yes("Close all Claude Code sessions first. Copy MCP servers now?"):
        return
    src = json.loads(src_cfg.read_text(encoding="utf-8"))
    dst = json.loads(dst_cfg.read_text(encoding="utf-8"))
    servers = src.get("mcpServers", {})
    if not servers:
        print("Account 1 has no user-level MCP servers to copy.")
        return
    shutil.copy2(dst_cfg, backup_name(dst_cfg))
    dst["mcpServers"] = {**dst.get("mcpServers", {}), **servers}
    dst_cfg.write_text(json.dumps(dst, indent=2), encoding="utf-8")
    print(f"Copied {len(servers)} MCP server(s): {', '.join(servers)}")
    print(f"Check with:  claude{n} mcp list")


def cmd_undo(n):
    d = account_dir(n)
    if n < 2 or not d.exists():
        sys.exit(f"No account {n} found.")
    if not ask_yes(f"Remove links in {d} and restore backups? (account 1 is not touched)"):
        return
    for name in all_item_names():
        p = d / name
        if same_target(p, MAIN_DIR / name) or (is_link_or_junction(p) and not p.exists()):
            remove_link(p)
            print(f"  removed   {name}")
        backups = sorted(d.glob(f"{name}.bak-*"))
        if backups and not p.exists():
            backups[-1].rename(p)
            print(f"  restored  {name}")
    print(f"\nLinks removed. The login in {d} was kept; delete that folder to remove it fully.")
    if ask_yes(f"Also delete {d} (its login and backups) and the claude{n} shortcut?"):
        if any(is_link_or_junction(p) for p in d.iterdir()):
            sys.exit("Some links are still inside the folder; not deleting it for safety.")
        shutil.rmtree(d)
        write_shortcuts()
        print(f"  deleted {d} and updated shortcuts")


def main():
    ap = argparse.ArgumentParser(description="Run several Claude Code accounts on one computer.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="create or repair an account")
    s.add_argument("n", nargs="?", type=int)
    sub.add_parser("status", help="show accounts and link health")
    m = sub.add_parser("sync-mcp", help="copy account 1's MCP servers")
    m.add_argument("n", type=int)
    u = sub.add_parser("undo", help="remove an account's links")
    u.add_argument("n", type=int)
    a = ap.parse_args()
    {"setup": lambda: cmd_setup(a.n), "status": cmd_status,
     "sync-mcp": lambda: cmd_sync_mcp(a.n), "undo": lambda: cmd_undo(a.n)}[a.cmd]()


if __name__ == "__main__":
    main()
