#!/usr/bin/env python3
"""
data_analyst.py — Natural-language data analysis
Load CSV, JSON, or SQLite files and ask questions in plain English.
PAI generates and executes Python/Pandas analysis code locally.

Usage:
    python samples/data_analyst.py sales.csv
    python samples/data_analyst.py data.json --query "top 10 customers by revenue"
    python samples/data_analyst.py mydb.sqlite --table orders --query "monthly trend"
    python samples/data_analyst.py sales.csv --report  # full auto-report
"""

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

PAI_API = os.getenv("PAI_API", "http://localhost:9480")

ANALYSIS_PROMPT = """You are a data analyst expert who writes clean, correct Python/Pandas code.

## Dataset info
File: {filename}
Format: {fmt}
Shape: {shape}
Columns: {columns}
Sample (first 5 rows):
{sample}

## Task
{query}

Write Python code that:
1. Loads the data from the file path variable `DATA_PATH`
2. Performs the analysis
3. Prints a clear result (table, summary stats, or narrative)
4. Saves any plot to 'analysis_plot.png' if a chart is needed

Rules:
- Use pandas, numpy, matplotlib (already installed)
- Do NOT import the data inline — use DATA_PATH
- Print final results clearly formatted
- Output ONLY valid Python — no markdown, no explanation

DATA_PATH = "{filepath}"
"""

REPORT_PROMPT = """You are a data analyst. Generate a comprehensive automated report for this dataset.

## Dataset
File: {filename}
Format: {fmt}
Shape: {shape}
Columns: {columns}
Sample:
{sample}

Write Python code that produces a full report including:
1. Dataset overview (shape, dtypes, missing values)
2. Descriptive statistics for numeric columns
3. Distribution of categorical columns (top 10 values)
4. Correlation matrix (if numeric columns ≥ 2)
5. Top insights (3–5 bullet points)
6. Anomalies (outliers, duplicates, null rates)

Print results clearly. Use only pandas, numpy, and matplotlib.
DATA_PATH = "{filepath}"
Output ONLY valid Python.
"""


def _call_pai(prompt: str) -> str:
    payload = json.dumps({
        "prompt": prompt, "model": "sudo",
        "max_tokens": 2500, "web_rag": False,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())["response"]


def _load_sample(filepath: Path, fmt: str) -> tuple:
    """Load dataset and return (shape_str, columns_str, sample_str)."""
    try:
        import pandas as pd
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pandas", "-q"])
        import pandas as pd

    if fmt == "csv":
        df = pd.read_csv(filepath)
    elif fmt == "json":
        df = pd.read_json(filepath)
    elif fmt == "sqlite":
        import sqlite3
        con = sqlite3.connect(str(filepath))
        tables = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table'", con)
        table  = tables.iloc[0]["name"]
        df     = pd.read_sql(f"SELECT * FROM '{table}' LIMIT 1000", con)
    else:
        raise ValueError(f"Unsupported format: {fmt}")

    shape   = f"{df.shape[0]:,} rows × {df.shape[1]} columns"
    columns = ", ".join(f"{c} ({df[c].dtype})" for c in df.columns[:20])
    buf     = io.StringIO()
    df.head(5).to_string(buf, index=False)
    sample  = buf.getvalue()
    return df, shape, columns, sample


def _run_code(code: str, filepath: Path) -> None:
    """Execute generated Python code in a subprocess."""
    # Strip markdown fences
    if "```python" in code:
        code = code.split("```python", 1)[-1].split("```")[0]
    elif "```" in code:
        code = code.split("```", 1)[-1].split("```")[0]
    code = code.strip()

    # Inject DATA_PATH
    if "DATA_PATH" not in code:
        code = f'DATA_PATH = "{filepath}"\n' + code

    print("\n" + "="*60 + " Generated code:")
    print(code[:800] + ("…" if len(code) > 800 else ""))
    print("="*60 + " Output:")

    fd, tmp = tempfile.mkstemp(suffix=".py")
    try:
        os.write(fd, code.encode())
        os.close(fd)
        subprocess.run([sys.executable, tmp], check=False)
    finally:
        os.unlink(tmp)


def main():
    ap = argparse.ArgumentParser(description="PAI Data Analyst")
    ap.add_argument("file",    help="Data file (CSV, JSON, SQLite)")
    ap.add_argument("--query", metavar="Q",    help="Natural-language analysis question")
    ap.add_argument("--report",action="store_true", help="Generate full automated report")
    ap.add_argument("--table", metavar="TABLE",help="SQLite table name")
    ap.add_argument("--code",  action="store_true", help="Print generated code only (don't run)")
    args = ap.parse_args()

    filepath = Path(args.file).resolve()
    if not filepath.exists():
        print(f"[!] File not found: {filepath}")
        sys.exit(1)

    suffix = filepath.suffix.lower()
    fmt    = {"csv": "csv", ".json": "json", ".sqlite": "sqlite",
              ".db": "sqlite", ".jsonl": "json"}.get(suffix, "csv")

    print(f"  Loading {filepath.name}…", end="", flush=True)
    df, shape, columns, sample = _load_sample(filepath, fmt)
    print(f" {shape}")

    if args.report:
        prompt = REPORT_PROMPT.format(
            filename=filepath.name, fmt=fmt, shape=shape,
            columns=columns, sample=sample, filepath=filepath,
        )
    elif args.query:
        prompt = ANALYSIS_PROMPT.format(
            filename=filepath.name, fmt=fmt, shape=shape,
            columns=columns, sample=sample,
            query=args.query, filepath=filepath,
        )
    else:
        # Interactive mode
        print(f"\nDataset: {shape} | Columns: {columns[:80]}")
        print("Ask a question (or 'report' for full analysis, 'quit' to exit):")
        while True:
            try:
                q = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q.lower() in ("quit", "exit", "q"):
                break
            if q.lower() == "report":
                args.report = True
                prompt = REPORT_PROMPT.format(
                    filename=filepath.name, fmt=fmt, shape=shape,
                    columns=columns, sample=sample, filepath=filepath,
                )
            else:
                prompt = ANALYSIS_PROMPT.format(
                    filename=filepath.name, fmt=fmt, shape=shape,
                    columns=columns, sample=sample,
                    query=q, filepath=filepath,
                )
            print("  Generating…", end="", flush=True)
            code = _call_pai(prompt)
            print(" done.")
            _run_code(code, filepath)
        return

    print("  Generating analysis code…", end="", flush=True)
    code = _call_pai(prompt)
    print(" done.")

    if args.code:
        print(code)
    else:
        _run_code(code, filepath)


if __name__ == "__main__":
    main()
