# Claude Code Accounts

Run several Claude Code accounts side by side on one computer. Each account keeps its own login and usage limits, and all of them share one set of skills, settings, instructions and chat history.

Works on Windows and macOS, with a small window (GUI) or from the command line. No dependencies beyond Python.

> Unofficial project. Not affiliated with or endorsed by Anthropic. Claude and Claude Code are trademarks of Anthropic.

## How it works

Claude Code keeps everything for an account in one folder, `~/.claude`, and reads the `CLAUDE_CONFIG_DIR` environment variable to use a different folder instead. This tool gives each extra account its own folder (`~/.claude-account2`, `~/.claude-account3`, …) so it has its own login, then links the shareable parts of that folder back to `~/.claude`. Because they're links rather than copies, a skill you add or a chat you start in one account shows up in the others straight away.

It only uses Claude Code's own settings and the official `claude` program. Nothing is patched or intercepted.

## Requirements

- Claude Code installed, and logged in once with your main account (`claude`, then sign in)
- Python 3.9 or newer (the command-line script also runs on 3.8)
- For the window on a Mac with Homebrew Python: `brew install python-tk`

## Quick start: the window

Keep both `.py` files in the same folder, then run:

```
python claude_accounts_gui.py        # Windows
python3 claude_accounts_gui.py       # macOS
```

1. Click **Add account**. This creates `claude2` and links it to your main account.
2. With `claude2` selected, click **Open terminal** and sign in with your second account.
3. Click **Copy MCP servers** to give it the same MCP servers as your main account.

After that, select any account and click **Open terminal** (or double-click it) to start Claude Code with that account in the folder shown under "Open terminals in".

## Quick start: command line

```
python claude_accounts.py setup         # creates the next account (claude2, claude3, …)
```

Open a new terminal, run `claude2`, sign in once with the second account, then:

```
python claude_accounts.py sync-mcp 2
```

From then on, type `claude1`, `claude2` and so on to start Claude Code with that account. Each one can run in its own terminal at the same time.

| Command | What it does |
|---|---|
| `setup [N]` | Create account N, or repair its links if they stopped working |
| `status` | Show every account and whether its links are healthy |
| `sync-mcp N` | Copy your main account's MCP servers to account N |
| `undo N` | Remove account N's links and restore anything that was backed up |
| `--version` | Show the version |

Use `python3` instead of `python` on macOS.

## What's shared

Every account uses account 1's:

- skills, agents, commands, plugins, hooks, rules and output styles
- global `CLAUDE.md` and `settings.json`
- chat history (`/resume` and `--continue` work across accounts), saved plans, checkpoints for `/rewind`, subagent memory and todos
- prompt history (the up arrow), and `keybindings.json` once account 1 has one (run `setup N` again after creating it)

Never shared, so each account stays separate: the login (`.credentials.json`, or the macOS Keychain), `.claude.json` (account details, MCP servers, approvals), organization policy files, and temporary runtime folders.

### Using a work account and a personal account?

Sharing chat history means work conversations become visible to, and resumable from, your personal account. If one account belongs to your employer, edit the lists at the top of `claude_accounts.py` and remove `projects`, `plans`, `file-history`, `todos` and `agent-memory` from `SHARED_FOLDERS` before running `setup`. Skills and settings can still be shared.

## Safety

- Nothing is deleted. If account N already has its own version of something, it's renamed to `name.bak-<date>` before the link is made.
- Account N's chats, plans and checkpoints are merged into account 1 before linking, so none are lost. If the same file exists in both, account 1's copy is kept and account N's is kept in the backup.
- Config folders such as skills and rules are backed up, not merged. Account 1 stays the source of truth.
- `undo` removes only links and restores the backups. It never touches account 1.
- The shortcuts live between `# >>> claude-accounts >>>` markers in your PowerShell profile or `~/.zshrc`. The rest of the file is left exactly as it was, including its encoding and line endings.
- On Windows, folders are linked with junctions and files with symbolic links, or hard links if your account can't create symbolic links. No admin rights are needed.

## Check that it worked

After setting up an account:

1. `python claude_accounts.py status`: every item says `linked`
2. Start `claude2` and type `/status`: it shows your second account's email
3. Start `claude1` and type `/status`: it still shows your main account's email
4. In `claude2`, type `/resume`: your `claude1` chats are listed

## Troubleshooting

**"claude2 is not recognized" on Windows.** Open a new PowerShell window. If it still fails, your PowerShell execution policy is blocking your profile. Setup offers to fix this. If your organization's Group Policy blocks it, use the window's **Open terminal** button instead, which doesn't depend on the profile.

**`status` shows "NOT LINKED".** Run `setup N` again (or click **Repair links**). It backs up whatever replaced the link and links it again.

**macOS says the app isn't allowed to control Terminal.** Allow it in System Settings > Privacy & Security > Automation, then try again.

**After a Claude Code update.** Run `status`. If a new version stores something new in its folder, `setup N` picks up anything this tool knows to share.

## Running the tests

```
python -m unittest discover -s tests -v
```

The tests use a throwaway home folder and a fake `claude`, so they never touch your real setup. Some run only when their tools are present: zsh, PowerShell, a display for the window tests (on Linux, use `xvfb-run`), and Windows itself for `test_windows.py`. To also check against the real Claude Code, set `CCA_REAL_CLAUDE=1`.

GitHub Actions runs the suite on Linux (Python 3.9–3.13), macOS and Windows on every push, and once a week to catch Claude Code updates that change how it stores things.

## Responsible use

Use this for your own accounts. Having more than one account is fine, but don't use this tool to share an account with someone else, resell access, get around a suspension, or switch between accounts to get around usage limits. See Anthropic's [Usage Policy](https://www.anthropic.com/legal/aup) and [Consumer Terms](https://www.anthropic.com/legal/consumer-terms).

## License

MIT. See [LICENSE](LICENSE).
