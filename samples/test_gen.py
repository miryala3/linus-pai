#!/usr/bin/env python3
"""
test_gen.py — Automatic test suite generator
Reads source files and generates pytest-compatible test suites
covering happy paths, edge cases, and error conditions.

Usage:
    python samples/test_gen.py mymodule.py
    python samples/test_gen.py src/ --out tests/
    python samples/test_gen.py --missing src/   # only generate for untested functions
"""

import ast
import argparse
import json
import os
import urllib.request
from pathlib import Path
from typing import List

PAI_API = os.getenv("PAI_API", "http://localhost:9480")

TEST_PROMPT = """You are an expert Python test engineer.

Given the source code below, generate a complete pytest test suite that covers:
1. All public functions and methods
2. Happy path (expected inputs)
3. Edge cases (empty, None, zero, max values)
4. Error paths (wrong types, invalid args, exceptions)
5. Boundary conditions

Rules:
- Use pytest fixtures where appropriate
- Use parametrize for multiple input cases
- Include docstrings explaining what each test validates
- Mock external dependencies (file I/O, network, databases)
- Generate runnable code — no placeholders

Module name: {module_name}

Source code:
```python
{source}
```

Output ONLY the test file content — no explanation text.
"""


def _call_pai(prompt: str) -> str:
    payload = json.dumps({
        "prompt": prompt, "model": "sudo",
        "max_tokens": 3000, "web_rag": False,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["response"]


def _extract_public_functions(source: str) -> List[str]:
    """Return names of public functions/methods in source."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                names.append(node.name)
    return names


def generate_tests(src_path: Path, out_dir: Path | None = None) -> Path:
    source = src_path.read_text(errors="ignore")
    module  = src_path.stem
    funcs   = _extract_public_functions(source)

    print(f"  Generating tests for {src_path.name} "
          f"({len(funcs)} public functions: {', '.join(funcs[:6])}{'…' if len(funcs)>6 else ''})…",
          end="", flush=True)

    # Truncate to 10k chars to stay within context
    if len(source) > 10_000:
        source = source[:10_000] + "\n# ... (truncated)"

    prompt = TEST_PROMPT.format(module_name=module, source=source)
    test_code = _call_pai(prompt)

    # Strip markdown fences if model returned them
    if "```python" in test_code:
        test_code = test_code.split("```python", 1)[-1].split("```")[0]
    elif "```" in test_code:
        test_code = test_code.split("```", 1)[-1].rsplit("```", 1)[0]

    # Write test file
    out = (out_dir or src_path.parent) / f"test_{module}.py"
    out.write_text(test_code.strip() + "\n")
    print(f" → {out}")
    return out


def find_untested(src_dir: Path, test_dir: Path) -> List[Path]:
    """Return source files with no corresponding test file."""
    untested = []
    for src in src_dir.rglob("*.py"):
        if src.name.startswith("test_") or src.name.startswith("_"):
            continue
        test_file = test_dir / f"test_{src.stem}.py"
        if not test_file.exists():
            untested.append(src)
    return untested


def main():
    ap = argparse.ArgumentParser(description="PAI Test Generator")
    ap.add_argument("source",   nargs="?",     help="Source file or directory")
    ap.add_argument("--out",    metavar="DIR", help="Output directory for test files")
    ap.add_argument("--missing",metavar="DIR", help="Generate only for files with no tests")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    if args.missing:
        src_dir  = Path(args.missing)
        test_dir = out_dir or src_dir
        files    = find_untested(src_dir, test_dir)
        if not files:
            print("All source files already have tests.")
            return
        print(f"Found {len(files)} untested files:")
        for f in files:
            generate_tests(f, out_dir)
        return

    if not args.source:
        ap.print_help()
        return

    src = Path(args.source)
    if src.is_dir():
        for f in src.rglob("*.py"):
            if not f.name.startswith("test_"):
                generate_tests(f, out_dir)
    else:
        generate_tests(src, out_dir)


if __name__ == "__main__":
    main()
