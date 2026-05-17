#!/usr/bin/env python3
"""
bug_fixer.py — Error-driven automatic bug fixing
Reads a traceback or error output, locates the relevant source file,
and generates a corrected version with explanation.

Usage:
    python myapp.py 2>&1 | python samples/bug_fixer.py
    python samples/bug_fixer.py --error error.log --src src/
    python samples/bug_fixer.py --loop myapp.py   # run → fix → run until clean
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

PAI_API = os.getenv("PAI_API", "http://localhost:9480")

FIX_PROMPT = """You are an expert debugger. You will fix a Python bug.

## Error output
```
{error}
```

## Source file: {filename}
```python
{source}
```

Instructions:
1. Identify the root cause in ONE sentence.
2. Output the COMPLETE fixed file — no truncation, no placeholders.
3. After the file, write:
   ROOT_CAUSE: <one sentence>
   CHANGES: <bullet list of every line changed>

Output format:
```python
<complete fixed source>
```
ROOT_CAUSE: ...
CHANGES:
- ...
"""


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


def extract_file_from_traceback(error: str, src_root: str = ".") -> Optional[Path]:
    """Find the most-likely source file from a Python traceback."""
    # Match: File "path/to/file.py", line N
    matches = re.findall(r'File "([^"]+\.py)", line \d+', error)
    for m in reversed(matches):  # last entry is usually the crash point
        p = Path(m)
        if p.exists():
            return p
        # Try relative to src_root
        rel = Path(src_root) / p
        if rel.exists():
            return rel
    return None


def parse_response(response: str) -> Tuple[str, str, str]:
    """Returns (fixed_source, root_cause, changes)."""
    fixed = ""
    root_cause = ""
    changes = ""

    if "```python" in response:
        fixed = response.split("```python", 1)[-1].split("```")[0].strip()
    elif "```" in response:
        fixed = response.split("```", 1)[-1].split("```")[0].strip()

    rc_m = re.search(r"ROOT_CAUSE:\s*(.+)", response)
    if rc_m:
        root_cause = rc_m.group(1).strip()

    ch_m = re.search(r"CHANGES:\s*\n((?:- .+\n?)+)", response)
    if ch_m:
        changes = ch_m.group(1).strip()

    return fixed, root_cause, changes


def fix_file(error: str, src_file: Path, dry_run: bool = False) -> bool:
    original = src_file.read_text(errors="ignore")
    prompt   = FIX_PROMPT.format(
        error=error[:3000],
        filename=src_file.name,
        source=original[:8000],
    )

    print(f"  Analysing {src_file}…", end="", flush=True)
    response   = _call_pai(prompt)
    fixed, rc, changes = parse_response(response)
    print(" done.")

    if not fixed:
        print("[!] Could not extract fixed source from response.")
        print(response[:1000])
        return False

    # Show diff
    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        fixed.splitlines(keepends=True),
        fromfile=f"a/{src_file.name}",
        tofile=f"b/{src_file.name}",
    ))

    print(f"\nRoot cause: {rc}")
    print(f"Changes:\n  {changes.replace(chr(10), chr(10)+'  ')}")
    print(f"\nDiff ({len(diff)} lines):")
    for line in diff[:60]:
        colour = "\033[31m" if line.startswith("-") else ("\033[32m" if line.startswith("+") else "")
        print(f"{colour}{line.rstrip()}\033[0m")
    if len(diff) > 60:
        print(f"  … {len(diff)-60} more lines")

    if dry_run:
        print("\n[dry-run] Not writing. Pass --apply to save.")
        return True

    backup = src_file.with_suffix(f".bak{src_file.suffix}")
    backup.write_text(original)
    src_file.write_text(fixed)
    print(f"\n✓ Fixed {src_file}  (backup → {backup})")
    return True


def run_and_fix_loop(script: Path, max_iterations: int = 5) -> None:
    """Run a script; if it errors, fix it; repeat until clean or max iterations."""
    for i in range(max_iterations):
        print(f"\n{'='*50} Run {i+1}/{max_iterations}")
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            print("✓ Script exited cleanly.")
            return
        error = result.stderr + result.stdout
        print(f"Error:\n{error[:500]}")
        src = extract_file_from_traceback(error) or script
        if not fix_file(error, src, dry_run=False):
            print("Fix failed — stopping.")
            return
    print(f"Reached max iterations ({max_iterations}).")


def main():
    ap = argparse.ArgumentParser(description="PAI Bug Fixer")
    ap.add_argument("--error",  metavar="FILE",   help="Error log file")
    ap.add_argument("--src",    metavar="FILE",   help="Source file to fix (auto-detected if omitted)")
    ap.add_argument("--srcdir", metavar="DIR",    help="Search root for auto-detection", default=".")
    ap.add_argument("--loop",   metavar="SCRIPT", help="Run-fix loop until script passes")
    ap.add_argument("--apply",  action="store_true", help="Write fix to disk (default: dry-run)")
    args = ap.parse_args()

    if args.loop:
        run_and_fix_loop(Path(args.loop))
        return

    # Read error from file or stdin
    if args.error:
        error = Path(args.error).read_text()
    elif not sys.stdin.isatty():
        error = sys.stdin.read()
    else:
        print("Paste error output (Ctrl-D when done):")
        error = sys.stdin.read()

    # Locate source file
    if args.src:
        src = Path(args.src)
    else:
        src = extract_file_from_traceback(error, args.srcdir)
        if src is None:
            print("[!] Could not auto-detect source file. Use --src.")
            sys.exit(1)

    fix_file(error, src, dry_run=not args.apply)


if __name__ == "__main__":
    main()
