#!/usr/bin/env python3
"""
commit_writer.py — Smart git commit message generator
Reads staged changes, understands context from the codebase, and
writes a Conventional Commits-format message with body and breaking-change footer.

Usage:
    python samples/commit_writer.py           # generate and print
    python samples/commit_writer.py --commit  # generate and git commit immediately
    python samples/commit_writer.py --amend   # amend last commit message
    python samples/commit_writer.py --hook    # install as a prepare-commit-msg git hook
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

PAI_API = os.getenv("PAI_API", "http://localhost:9480")

COMMIT_PROMPT = """You are an expert at writing git commit messages following Conventional Commits.

Given the diff below, write a commit message with:
- A subject line: <type>(<scope>): <short description>  (max 72 chars)
  type: feat | fix | docs | style | refactor | test | chore | perf | ci | build
- A blank line
- A body: what changed and WHY (not what — the diff shows what)
- If breaking change: BREAKING CHANGE: <description> footer

Output ONLY the commit message — no explanation, no quotes, no markdown.

## Staged diff
```diff
{diff}
```

## Recent commit messages (for style reference)
{recent}
"""


def _call_pai(prompt: str) -> str:
    payload = json.dumps({
        "prompt": prompt, "model": "sized",
        "max_tokens": 512, "web_rag": False,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["response"].strip()


def _get_diff() -> str:
    # Try staged first, then all changes
    for cmd in (["git", "diff", "--cached"], ["git", "diff"]):
        try:
            diff = subprocess.check_output(cmd, text=True)
            if diff.strip():
                return diff[:6000]
        except subprocess.CalledProcessError:
            pass
    return ""


def _get_recent_commits(n: int = 5) -> str:
    try:
        return subprocess.check_output(
            ["git", "log", f"-{n}", "--oneline"], text=True
        )
    except subprocess.CalledProcessError:
        return ""


def generate_message() -> str:
    diff   = _get_diff()
    recent = _get_recent_commits()

    if not diff:
        print("[!] No staged or unstaged changes found.")
        sys.exit(1)

    prompt = COMMIT_PROMPT.format(diff=diff, recent=recent)
    print("  Generating commit message…", end="", flush=True)
    msg = _call_pai(prompt)
    print(" done.")
    return msg


def install_hook() -> None:
    hook_dir = Path(".git/hooks")
    if not hook_dir.exists():
        print("[!] Not a git repo (no .git/hooks directory).")
        sys.exit(1)
    hook_path = hook_dir / "prepare-commit-msg"
    script_path = Path(__file__).resolve()
    hook = f"""#!/usr/bin/env bash
# Installed by commit_writer.py
COMMIT_MSG_FILE=$1
COMMIT_SOURCE=$2

# Only auto-generate if no message provided and not amending
if [ -z "$COMMIT_SOURCE" ]; then
    python3 "{script_path}" > /tmp/pai_commit_msg.txt 2>/dev/null
    if [ $? -eq 0 ] && [ -s /tmp/pai_commit_msg.txt ]; then
        cat /tmp/pai_commit_msg.txt > "$COMMIT_MSG_FILE"
    fi
fi
"""
    hook_path.write_text(hook)
    hook_path.chmod(0o755)
    print(f"✓ Hook installed at {hook_path}")
    print("  Now every 'git commit' will auto-generate a message.")


def main():
    ap = argparse.ArgumentParser(description="PAI Commit Message Generator")
    ap.add_argument("--commit", action="store_true", help="Also run git commit with the message")
    ap.add_argument("--amend",  action="store_true", help="Amend last commit message")
    ap.add_argument("--hook",   action="store_true", help="Install as git prepare-commit-msg hook")
    args = ap.parse_args()

    if args.hook:
        install_hook()
        return

    msg = generate_message()
    print("\n" + "="*60)
    print(msg)
    print("="*60 + "\n")

    if args.amend:
        subprocess.run(["git", "commit", "--amend", "-m", msg])
    elif args.commit:
        subprocess.run(["git", "commit", "-m", msg])
    else:
        print("Run with --commit to commit, or copy message above.")


if __name__ == "__main__":
    main()
