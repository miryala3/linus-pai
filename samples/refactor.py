#!/usr/bin/env python3
"""
refactor.py — AI-powered code refactoring
Modernises code, extracts functions, improves naming, adds type hints,
converts sync to async, and enforces project-specific patterns.

Usage:
    python samples/refactor.py old_code.py
    python samples/refactor.py src/ --goal "add type hints to all functions"
    python samples/refactor.py src/ --goal "convert callbacks to async/await"
    python samples/refactor.py src/ --goal "extract magic numbers to constants"
    python samples/refactor.py src/ --pattern patterns.md  # custom rules file
"""

import argparse
import difflib
import json
import os
import sys
import urllib.request
from pathlib import Path

PAI_API = os.getenv("PAI_API", "http://localhost:9480")

REFACTOR_PROMPT = """You are a senior Python engineer performing a targeted refactoring.

## Goal
{goal}

## Rules
- Preserve ALL existing functionality — do not change behaviour.
- Preserve ALL existing tests.
- Only make changes necessary to achieve the goal.
- Output the COMPLETE refactored file — no truncation.
- Output ONLY valid Python — no markdown fences.
- After the code, write:
  SUMMARY: one-line description of changes made

## Source file: {filename}
```python
{source}
```
"""

COMMON_GOALS = {
    "types":      "Add PEP 484 type annotations to all function signatures",
    "async":      "Convert synchronous blocking calls to async/await",
    "constants":  "Extract all magic numbers and repeated string literals to named constants",
    "functions":  "Break functions longer than 30 lines into smaller focused functions",
    "dataclasses":"Replace plain dicts and tuples with @dataclass or NamedTuple",
    "logging":    "Replace print() statements with proper logging calls",
    "pathlib":    "Replace os.path and string path operations with pathlib.Path",
    "walrus":     "Use walrus operator := where it simplifies code",
    "fstrings":   "Replace % and .format() string formatting with f-strings",
    "exceptions": "Replace bare except: with specific exception types",
}


def _call_pai(prompt: str) -> str:
    payload = json.dumps({
        "prompt": prompt, "model": "sudo",
        "max_tokens": 4000, "web_rag": False,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["response"]


def refactor_file(src: Path, goal: str, apply: bool = False) -> str:
    source = src.read_text(errors="ignore")
    if len(source) > 10_000:
        print(f"  [!] {src.name} is large ({len(source):,} chars), truncating to 10k")
        source = source[:10_000]

    prompt = REFACTOR_PROMPT.format(
        goal=goal, filename=src.name, source=source,
    )
    print(f"  Refactoring {src.name}…", end="", flush=True)
    result = _call_pai(prompt)
    print(" done.")

    # Parse out summary
    summary = ""
    if "SUMMARY:" in result:
        summary = result.split("SUMMARY:")[-1].strip().splitlines()[0]
        result  = result.split("SUMMARY:")[0].strip()

    # Strip markdown fences
    if "```python" in result:
        result = result.split("```python", 1)[-1].split("```")[0]
    result = result.strip()

    # Show diff
    original = src.read_text(errors="ignore")
    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        result.splitlines(keepends=True),
        fromfile=f"a/{src.name}", tofile=f"b/{src.name}",
    ))

    print(f"\n  Summary: {summary}")
    print(f"  Diff: {len(diff)} lines changed")
    for line in diff[:40]:
        c = "\033[31m" if line.startswith("-") else ("\033[32m" if line.startswith("+") else "")
        print(f"  {c}{line.rstrip()}\033[0m")
    if len(diff) > 40:
        print(f"  … {len(diff)-40} more lines")

    if apply:
        bak = src.with_suffix(".pre_refactor.py")
        bak.write_text(original)
        src.write_text(result)
        print(f"\n  ✓ Written to {src}  (original → {bak.name})")

    return result


def main():
    ap = argparse.ArgumentParser(
        description="PAI Refactor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Built-in goals:\n" + "\n".join(f"  {k}: {v}" for k, v in COMMON_GOALS.items()),
    )
    ap.add_argument("source",     nargs="?",     help="Source file or directory")
    ap.add_argument("--goal",     metavar="GOAL",help="Refactoring goal (free text or shorthand)")
    ap.add_argument("--pattern",  metavar="FILE",help="Markdown file with project conventions")
    ap.add_argument("--apply",    action="store_true", help="Write changes to disk")
    ap.add_argument("--list-goals",action="store_true",help="List built-in goals")
    args = ap.parse_args()

    if args.list_goals:
        for k, v in COMMON_GOALS.items():
            print(f"  {k:15} {v}")
        return

    if not args.source:
        ap.print_help()
        return

    goal = args.goal or "types"
    goal = COMMON_GOALS.get(goal, goal)   # expand shorthand

    if args.pattern:
        extra = Path(args.pattern).read_text()
        goal  = f"{goal}\n\nAdditional project conventions:\n{extra}"

    src = Path(args.source)
    files = list(src.rglob("*.py")) if src.is_dir() else [src]
    files = [f for f in files if not f.name.startswith("test_")]

    for f in files:
        refactor_file(f, goal, apply=args.apply)


if __name__ == "__main__":
    main()
