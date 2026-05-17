#!/usr/bin/env python3
"""
doc_writer.py — AI documentation generator
Writes docstrings, README files, and API references for any codebase.

Usage:
    python samples/doc_writer.py mymodule.py        # add docstrings in-place
    python samples/doc_writer.py src/ --readme       # write README.md
    python samples/doc_writer.py src/ --api-ref      # write API reference (Markdown)
    python samples/doc_writer.py mymodule.py --dry   # preview without writing
"""

import argparse
import ast
import json
import os
import re
import urllib.request
from pathlib import Path

PAI_API = os.getenv("PAI_API", "http://localhost:9480")

DOCSTRING_PROMPT = """Add Google-style docstrings to every public function, method, and class
that is missing one in the Python source below.

Rules:
- Do NOT change any logic, imports, or signatures.
- Only ADD or REPLACE docstrings.
- Docstrings must include Args:, Returns:, Raises: sections where relevant.
- Output the COMPLETE file with all docstrings added — no placeholders.
- Output ONLY valid Python — no markdown fences.

Source:
```python
{source}
```
"""

README_PROMPT = """Write a professional README.md for this Python project.

Structure:
# Project Name
Short description.

## Features
Bullet list.

## Installation
```bash
pip install ...
```

## Quick Start
Minimal working example.

## API Reference
Key classes/functions with signatures and examples.

## Configuration
Environment variables and config options.

## Contributing
Brief guide.

---
Source files:
{source_summary}
"""

API_REF_PROMPT = """Write a complete API reference in Markdown for the following Python module.

For each public class and function include:
- Signature
- Description
- Parameters (type, description)
- Return value
- Raises
- Example

Module: {module}
Source:
```python
{source}
```
"""


def _call_pai(prompt: str, max_tokens: int = 4000) -> str:
    payload = json.dumps({
        "prompt": prompt, "model": "sudo",
        "max_tokens": max_tokens, "web_rag": False,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["response"]


def _functions_missing_docstrings(source: str) -> list:
    """Return list of function names without docstrings."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    missing = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                doc = ast.get_docstring(node)
                if not doc:
                    missing.append(node.name)
    return missing


def add_docstrings(src_path: Path, dry_run: bool = False) -> None:
    source  = src_path.read_text(errors="ignore")
    missing = _functions_missing_docstrings(source)

    if not missing:
        print(f"  {src_path.name} — all public items already documented.")
        return

    print(f"  {src_path.name} — {len(missing)} undocumented: {', '.join(missing[:5])}…",
          end="", flush=True)

    prompt   = DOCSTRING_PROMPT.format(source=source[:10_000])
    result   = _call_pai(prompt)

    # Strip any accidental markdown fences
    if "```python" in result:
        result = result.split("```python", 1)[-1].split("```")[0]
    result = result.strip()

    if dry_run:
        print(f"\n[DRY] Would write {len(result)} chars to {src_path}")
        print(result[:400] + "…")
        return

    backup = src_path.with_suffix(".bak.py")
    backup.write_text(source)
    src_path.write_text(result)
    print(f" done. (backup → {backup.name})")


def _summarise_dir(src_dir: Path) -> str:
    lines = []
    for f in sorted(src_dir.rglob("*.py")):
        if f.name.startswith("_"):
            continue
        source = f.read_text(errors="ignore")[:3000]
        lines.append(f"### {f.relative_to(src_dir)}\n```python\n{source}\n```")
    return "\n\n".join(lines[:8])   # limit to first 8 files


def write_readme(src_dir: Path, out: Path | None = None) -> None:
    print(f"  Generating README for {src_dir}…", end="", flush=True)
    summary = _summarise_dir(src_dir)
    prompt  = README_PROMPT.format(source_summary=summary)
    result  = _call_pai(prompt, max_tokens=3000)
    dest    = out or (src_dir / "README.md")
    dest.write_text(result)
    print(f" → {dest}")


def write_api_ref(src_path: Path, out: Path | None = None) -> None:
    source = src_path.read_text(errors="ignore")[:10_000]
    print(f"  Generating API reference for {src_path.name}…", end="", flush=True)
    prompt = API_REF_PROMPT.format(module=src_path.stem, source=source)
    result = _call_pai(prompt, max_tokens=3000)
    dest   = out or src_path.with_suffix(".api.md")
    dest.write_text(result)
    print(f" → {dest}")


def main():
    ap = argparse.ArgumentParser(description="PAI Documentation Writer")
    ap.add_argument("source",    nargs="?",      help="Source file or directory")
    ap.add_argument("--readme",  action="store_true", help="Generate README.md")
    ap.add_argument("--api-ref", action="store_true", help="Generate API reference")
    ap.add_argument("--dry",     action="store_true", help="Preview without writing")
    ap.add_argument("--out",     metavar="FILE",  help="Output file path")
    args = ap.parse_args()

    if not args.source:
        ap.print_help()
        return

    src = Path(args.source)
    out = Path(args.out) if args.out else None

    if args.readme:
        write_readme(src if src.is_dir() else src.parent, out)
    elif args.api_ref:
        targets = list(src.rglob("*.py")) if src.is_dir() else [src]
        for t in targets:
            write_api_ref(t, out)
    else:
        targets = list(src.rglob("*.py")) if src.is_dir() else [src]
        for t in targets:
            if not t.name.startswith("_") and not t.name.startswith("test_"):
                add_docstrings(t, dry_run=args.dry)


if __name__ == "__main__":
    main()
