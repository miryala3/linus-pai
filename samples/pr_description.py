#!/usr/bin/env python3
"""
pr_description.py — AI pull-request description writer
Generates a professional PR description from git diff + commit log.
Outputs GitHub-flavoured Markdown with summary, changes, testing notes.

Usage:
    python samples/pr_description.py                 # current branch vs main
    python samples/pr_description.py --base develop  # custom base branch
    python samples/pr_description.py --copy          # copy to clipboard
    python samples/pr_description.py --gh            # open GitHub PR in browser
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

PAI_API = os.getenv("PAI_API", "http://localhost:9480")

PR_PROMPT = """You are a senior engineer writing a pull request description.

Given the diff and commit log below, write a professional GitHub PR description in
GitHub-Flavoured Markdown that includes:

## Summary
2-3 sentences: what this PR does and why.

## Changes
Grouped bullet list of all meaningful changes (group by: feature / fix / refactor / test / docs).

## Testing
- What was manually tested
- Which automated tests cover this
- Steps for reviewer to verify

## Breaking Changes
List any breaking API or behaviour changes (or "None").

## Screenshots / Output
If the PR touches UI or CLI output, describe what it looks like (use code blocks for output).

---
## Commit log
{commits}

## Diff (truncated to 8000 chars)
```diff
{diff}
```

Output ONLY the PR description — no preamble, no quotes.
"""


def _call_pai(prompt: str) -> str:
    payload = json.dumps({
        "prompt": prompt, "model": "sudo",
        "max_tokens": 1500, "web_rag": False,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["response"]


def _git(cmd: list) -> str:
    try:
        return subprocess.check_output(["git"] + cmd, text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return ""


def get_branch() -> str:
    return _git(["branch", "--show-current"]).strip()


def get_diff(base: str) -> str:
    diff = _git(["diff", f"{base}...HEAD"])
    return diff[:8000] if diff else _git(["diff", "HEAD~1"])[:8000]


def get_commits(base: str) -> str:
    return _git(["log", f"{base}...HEAD", "--oneline", "--no-merges"])


def copy_to_clipboard(text: str) -> bool:
    """Try pbcopy (macOS), xclip, xsel (Linux), clip (Windows)."""
    for cmd in (["pbcopy"], ["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"], ["clip"]):
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            proc.communicate(text.encode())
            return proc.returncode == 0
        except FileNotFoundError:
            continue
    return False


def open_github_pr(branch: str, description: str) -> None:
    """Open browser to create a PR with pre-filled body (via gh CLI or URL)."""
    try:
        subprocess.run(
            ["gh", "pr", "create", "--fill", "--body", description],
            check=True,
        )
        return
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    # Fallback: open GitHub compare URL
    try:
        remote = _git(["remote", "get-url", "origin"]).strip()
        # Convert SSH to HTTPS
        remote = remote.replace("git@github.com:", "https://github.com/").rstrip(".git")
        url    = f"{remote}/compare/{branch}?expand=1"
        subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", url])
    except Exception as exc:
        print(f"Could not open browser: {exc}")


def main():
    ap = argparse.ArgumentParser(description="PAI PR Description Writer")
    ap.add_argument("--base",  default="main", help="Base branch (default: main)")
    ap.add_argument("--copy",  action="store_true", help="Copy to clipboard")
    ap.add_argument("--gh",    action="store_true", help="Open GitHub PR creation")
    ap.add_argument("--out",   metavar="FILE",      help="Save to file")
    args = ap.parse_args()

    branch  = get_branch()
    commits = get_commits(args.base)
    diff    = get_diff(args.base)

    if not commits and not diff:
        print(f"[!] No changes found between {args.base} and HEAD.")
        sys.exit(1)

    print(f"  Branch: {branch}  |  Base: {args.base}")
    print(f"  {len(commits.splitlines())} commits  |  {len(diff):,} diff chars")
    print("  Generating PR description…", end="", flush=True)

    prompt = PR_PROMPT.format(commits=commits, diff=diff)
    desc   = _call_pai(prompt)
    print(" done.\n")

    print("="*70)
    print(desc)
    print("="*70 + "\n")

    if args.copy:
        if copy_to_clipboard(desc):
            print("✓ Copied to clipboard.")
        else:
            print("[!] Could not copy to clipboard — install pbcopy/xclip/xsel.")

    if args.out:
        Path(args.out).write_text(desc)
        print(f"✓ Saved to {args.out}")

    if args.gh:
        open_github_pr(branch, desc)


if __name__ == "__main__":
    main()
