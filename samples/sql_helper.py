#!/usr/bin/env python3
"""
sql_helper.py — Natural language to SQL
Ask questions in English, get runnable SQL back, execute against any database.
Supports SQLite, PostgreSQL, MySQL (via connection string).

Usage:
    python samples/sql_helper.py mydb.sqlite
    python samples/sql_helper.py --dsn "postgresql://user:pass@localhost/mydb"
    python samples/sql_helper.py mydb.sqlite --query "top 10 customers by total spend"
    python samples/sql_helper.py mydb.sqlite --export results.csv
"""

import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path
from typing import List, Optional

PAI_API = os.getenv("PAI_API", "http://localhost:9480")

SQL_PROMPT = """You are an expert SQL developer.

## Database schema
{schema}

## Question
{question}

Write a single SQL query that answers the question. Rules:
- Use standard SQL compatible with {dialect}
- Include an ORDER BY clause where the result ordering matters
- LIMIT to 100 rows unless the question asks for all data
- Do not use CTEs unless necessary
- Output ONLY the SQL query — no explanation, no markdown fences

SQL:
"""

EXPLAIN_PROMPT = """Explain this SQL query in plain English:

```sql
{sql}
```

Database: {dialect}
Schema context: {schema}

Explain in 2-3 sentences what this query does, what it returns, and any performance notes.
"""


def _call_pai(prompt: str, model: str = "sized") -> str:
    payload = json.dumps({
        "prompt": prompt, "model": model,
        "max_tokens": 512, "web_rag": False,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["response"].strip()


class Database:
    def __init__(self, path: Optional[str] = None, dsn: Optional[str] = None):
        self.dialect = "SQLite"
        if dsn:
            if dsn.startswith("postgresql"):
                try:
                    import psycopg2
                    self.conn    = psycopg2.connect(dsn)
                    self.dialect = "PostgreSQL"
                except ImportError:
                    raise RuntimeError("pip install psycopg2-binary")
            elif dsn.startswith("mysql"):
                try:
                    import pymysql
                    self.conn    = pymysql.connect(**self._parse_mysql(dsn))
                    self.dialect = "MySQL"
                except ImportError:
                    raise RuntimeError("pip install pymysql")
            else:
                raise ValueError(f"Unsupported DSN: {dsn}")
        else:
            self.conn = sqlite3.connect(path or ":memory:")
            self.conn.row_factory = sqlite3.Row

    @staticmethod
    def _parse_mysql(dsn: str) -> dict:
        # mysql://user:pass@host:port/db
        m = re.match(r"mysql://([^:]+):([^@]+)@([^:/]+):?(\d+)?/(.+)", dsn)
        if not m:
            raise ValueError(f"Invalid MySQL DSN: {dsn}")
        return {"user": m[1], "password": m[2], "host": m[3],
                "port": int(m[4] or 3306), "db": m[5]}

    def schema(self) -> str:
        """Return the database schema as a string."""
        cur = self.conn.cursor()
        if self.dialect == "SQLite":
            cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")
            rows = cur.fetchall()
            return "\n\n".join(f"{r[0]}:\n  {r[1]}" for r in rows if r[1])
        elif self.dialect == "PostgreSQL":
            cur.execute("""
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
            """)
            tables: dict = {}
            for row in cur.fetchall():
                tables.setdefault(row[0], []).append(f"{row[1]} {row[2]}")
            return "\n".join(f"{t}({', '.join(cols)})" for t, cols in tables.items())
        return ""

    def execute(self, sql: str) -> tuple:
        """Execute SQL and return (columns, rows)."""
        cur = self.conn.cursor()
        cur.execute(sql)
        if cur.description:
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(200)
            return cols, rows
        self.conn.commit()
        return [], []

    def close(self):
        self.conn.close()


def _clean_sql(raw: str) -> str:
    raw = raw.strip()
    if "```sql" in raw:
        raw = raw.split("```sql", 1)[-1].split("```")[0]
    elif "```" in raw:
        raw = raw.split("```", 1)[-1].split("```")[0]
    return raw.strip().rstrip(";")


def _print_table(cols: List[str], rows: list) -> None:
    if not rows:
        print("  (no results)")
        return
    widths = [max(len(str(c)), max((len(str(r[i])) for r in rows), default=0))
              for i, c in enumerate(cols)]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    print(sep)
    print("| " + " | ".join(str(c).ljust(w) for c, w in zip(cols, widths)) + " |")
    print(sep)
    for row in rows[:50]:
        print("| " + " | ".join(str(v).ljust(w) for v, w in zip(row, widths)) + " |")
    if len(rows) > 50:
        print(f"  … {len(rows)-50} more rows")
    print(sep)
    print(f"  {len(rows)} row(s)")


def main():
    ap = argparse.ArgumentParser(description="PAI SQL Helper")
    ap.add_argument("db",     nargs="?",     help="SQLite file path")
    ap.add_argument("--dsn",  metavar="DSN", help="Database connection string")
    ap.add_argument("--query",metavar="Q",   help="Natural-language question (non-interactive)")
    ap.add_argument("--explain",metavar="SQL",help="Explain an existing SQL query")
    ap.add_argument("--export",metavar="CSV",help="Export last result to CSV")
    args = ap.parse_args()

    if not args.db and not args.dsn:
        ap.print_help()
        return

    db = Database(path=args.db, dsn=args.dsn)
    schema = db.schema()
    last_cols: List[str] = []
    last_rows: list = []

    print(f"  Connected ({db.dialect})  |  Schema: {len(schema.splitlines())} lines")

    if args.explain:
        prompt = EXPLAIN_PROMPT.format(sql=args.explain, dialect=db.dialect, schema=schema[:2000])
        print(_call_pai(prompt, model="sized"))
        return

    def _ask(question: str) -> None:
        nonlocal last_cols, last_rows
        prompt = SQL_PROMPT.format(schema=schema[:3000], question=question, dialect=db.dialect)
        print("  Generating SQL…", end="", flush=True)
        raw = _call_pai(prompt)
        sql = _clean_sql(raw)
        print(" done.")
        print(f"\n  SQL: {sql}\n")
        try:
            cols, rows = db.execute(sql)
            last_cols, last_rows = cols, rows
            _print_table(cols, rows)
        except Exception as exc:
            print(f"  [!] Query error: {exc}")

    if args.query:
        _ask(args.query)
    else:
        print("  Type a question in English (or SQL directly), 'schema' to view, 'quit' to exit:")
        while True:
            try:
                q = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if q.lower() in ("quit", "exit", "q"):
                break
            if q.lower() == "schema":
                print(schema)
                continue
            if q.upper().startswith(("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE")):
                try:
                    cols, rows = db.execute(q)
                    last_cols, last_rows = cols, rows
                    _print_table(cols, rows)
                except Exception as exc:
                    print(f"  [!] {exc}")
                continue
            _ask(q)

    if args.export and last_rows:
        with open(args.export, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(last_cols)
            w.writerows(last_rows)
        print(f"  ✓ Exported {len(last_rows)} rows to {args.export}")

    db.close()


if __name__ == "__main__":
    main()
