#!/usr/bin/env python3
"""
code_review.py — AI-powered local code review
Uses PAI (localhost:9480) to review any file or git diff for:
  bugs · security issues · code style · complexity · test coverage gaps

Usage:
    python samples/code_review.py myfile.py
    python samples/code_review.py --diff          # review staged git changes
    python samples/code_review.py --pr src/       # review all changed files in a dir
    python samples/code_review.py --watch src/    # auto-review on every save
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PAI_API = os.getenv("PAI_API", "http://localhost:9480")

REVIEW_PROMPT = """You are a senior software engineer performing a thorough code review.

Analyse the code below and provide a structured review covering:

## Summary
One sentence describing what the code does.

## Issues Found
For each issue, state:
- **Severity**: CRITICAL | HIGH | MEDIUM | LOW | NITPICK
- **Location**: function or line reference
- **Problem**: what is wrong
- **Fix**: concrete corrected code snippet

## Security
Any injection risks, credential exposure, unsafe deserialization, or OWASP issues.

## Performance
Algorithmic complexity, unnecessary allocations, blocking calls in async code.

## Missing Tests
List functions or branches that have no test coverage.

## Overall Score
X/10 — one line explanation.

---
Code to review:
```
{code}
```
"""


def _call_pai(prompt: str, model: str = "sudo") -> str:
    payload = json.dumps({
        "prompt": prompt, "model": model,
        "max_tokens": 2048, "web_rag": False,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())["response"]
    except Exception as exc:
        return f"[PAI error: {exc}]"


def review_file(path: Path) -> str:
    code = path.read_text(errors="ignore")
    if len(code) > 12_000:
        code = code[:12_000] + "\n... (truncated)"
    prompt = REVIEW_PROMPT.format(code=code)
    print(f"  Reviewing {path} ({len(code):,} chars)…", end="", flush=True)
    result = _call_pai(prompt)
    print(" done.")
    return result


def review_git_diff() -> str:
    try:
        diff = subprocess.check_output(
            ["git", "diff", "--cached"], text=True
        )
        if not diff.strip():
            diff = subprocess.check_output(["git", "diff"], text=True)
        if not diff.strip():
            return "[No changes to review]"
    except subprocess.CalledProcessError:
        return "[Not a git repository]"
    prompt = REVIEW_PROMPT.format(code=diff)
    print("  Reviewing git diff…", end="", flush=True)
    result = _call_pai(prompt)
    print(" done.")
    return result


def watch_directory(path: Path, extensions: tuple = (".py", ".js", ".ts", ".go")) -> None:
    print(f"Watching {path} for changes (Ctrl-C to stop)…")
    seen_mtime: dict = {}
    while True:
        for f in path.rglob("*"):
            if f.suffix not in extensions:
                continue
            try:
                mt = f.stat().st_mtime
            except OSError:
                continue
            if seen_mtime.get(str(f)) != mt:
                seen_mtime[str(f)] = mt
                print(f"\n{'='*60}")
                print(review_file(f))
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description="PAI Code Review")
    ap.add_argument("file",    nargs="?",      help="File to review")
    ap.add_argument("--diff",  action="store_true", help="Review git staged/unstaged diff")
    ap.add_argument("--pr",    metavar="DIR",  help="Review all .py files in DIR")
    ap.add_argument("--watch", metavar="DIR",  help="Auto-review on file changes")
    ap.add_argument("--out",   metavar="FILE", help="Save review to FILE")
    args = ap.parse_args()

    output = ""

    if args.diff:
        output = review_git_diff()
    elif args.pr:
        parts = []
        for f in Path(args.pr).rglob("*.py"):
            parts.append(f"### {f}\n\n" + review_file(f))
        output = "\n\n".join(parts)
    elif args.watch:
        watch_directory(Path(args.watch))
        return
    elif args.file:
        output = review_file(Path(args.file))
    else:
        ap.print_help()
        return

    print(f"\n{'='*60}")
    print(output)
    if args.out:
        Path(args.out).write_text(output)
        print(f"\nSaved to {args.out}")


if __name__ == "__main__":
    main()
