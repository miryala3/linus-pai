#!/usr/bin/env python3
"""
shell_helper.py — Natural-language shell command generator
Describe what you want in English; get the exact command back.
Runs with explanation + confirmation before executing.

Usage:
    python samples/shell_helper.py "find all files larger than 1GB modified this week"
    python samples/shell_helper.py "kill the process using port 8080"
    python samples/shell_helper.py "compress the logs/ folder to logs.tar.gz"
    python samples/shell_helper.py --safe    # explain only, never execute
    python samples/shell_helper.py --history # show recent generated commands
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

PAI_API  = os.getenv("PAI_API",   "http://localhost:9480")
HIST_FILE = Path.home() / ".linus_pai" / "shell_history.jsonl"

SHELL_PROMPT = """You are a shell command expert for {os_name}.

Task: {task}

Current directory: {cwd}
Shell: {shell}

Rules:
1. Output the EXACT command(s) on the FIRST line — nothing else on that line.
2. If multiple commands are needed, chain them with && or use a heredoc.
3. On the SECOND line write: EXPLANATION: <one sentence plain-English explanation>
4. On the THIRD line write: RISK: SAFE | CAUTION | DANGEROUS
   - SAFE: read-only, reversible
   - CAUTION: modifies files but recoverable
   - DANGEROUS: permanent deletion, system modification, network exposure

Example output:
find . -name "*.log" -mtime -7 -size +100M
EXPLANATION: Find log files modified in the last 7 days that are over 100 MB
RISK: SAFE
"""


def _call_pai(prompt: str) -> str:
    payload = json.dumps({
        "prompt": prompt, "model": "sized",
        "max_tokens": 256, "web_rag": False,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["response"]


def parse_response(response: str) -> tuple:
    lines  = response.strip().splitlines()
    cmd    = lines[0].strip() if lines else ""
    expl   = ""
    risk   = "UNKNOWN"
    for line in lines[1:]:
        if line.startswith("EXPLANATION:"):
            expl = line.split(":", 1)[-1].strip()
        elif line.startswith("RISK:"):
            risk = line.split(":", 1)[-1].strip()
    return cmd, expl, risk


def _save_history(task: str, cmd: str, executed: bool) -> None:
    HIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HIST_FILE, "a") as f:
        f.write(json.dumps({
            "ts": datetime.now().isoformat(),
            "task": task, "cmd": cmd, "executed": executed,
        }) + "\n")


def show_history(n: int = 20) -> None:
    if not HIST_FILE.exists():
        print("No history yet.")
        return
    lines = HIST_FILE.read_text().strip().splitlines()[-n:]
    for line in lines:
        entry = json.loads(line)
        mark  = "✓" if entry.get("executed") else "·"
        print(f"  {mark} {entry['ts'][:16]}  {entry['cmd'][:70]}")
        print(f"       [{entry['task']}]")


RISK_COLORS = {
    "SAFE":      "\033[32m",
    "CAUTION":   "\033[33m",
    "DANGEROUS": "\033[31m",
}
RESET = "\033[0m"


def main():
    ap = argparse.ArgumentParser(description="PAI Shell Helper")
    ap.add_argument("task",     nargs="?", help="What you want to do (in plain English)")
    ap.add_argument("--safe",   action="store_true", help="Explain only — never execute")
    ap.add_argument("--yes",    action="store_true", help="Execute without confirmation")
    ap.add_argument("--history",action="store_true", help="Show recent commands")
    args = ap.parse_args()

    if args.history:
        show_history()
        return

    if not args.task:
        ap.print_help()
        return

    os_name = platform.system()
    shell   = os.getenv("SHELL", "/bin/bash")

    prompt  = SHELL_PROMPT.format(
        task=args.task, os_name=os_name,
        cwd=os.getcwd(), shell=shell,
    )

    print("  Generating command…", end="", flush=True)
    response = _call_pai(prompt)
    cmd, expl, risk = parse_response(response)
    print(" done.\n")

    if not cmd:
        print("Could not generate a command. Try rephrasing.")
        return

    color = RISK_COLORS.get(risk, "")
    print(f"  Command    : \033[1m{cmd}\033[0m")
    print(f"  Explanation: {expl}")
    print(f"  Risk       : {color}{risk}{RESET}\n")

    _save_history(args.task, cmd, executed=False)

    if args.safe:
        print("(--safe mode: not executing)")
        return

    if risk == "DANGEROUS" and not args.yes:
        print("⚠  DANGEROUS operation. Type 'EXECUTE' to confirm, or press Enter to cancel:")
        confirm = input("> ").strip()
        if confirm != "EXECUTE":
            print("Cancelled.")
            return
    elif not args.yes and risk != "SAFE":
        print("Run this command? [y/N] ", end="")
        ans = input().strip().lower()
        if ans not in ("y", "yes"):
            print("Cancelled.")
            return

    print(f"  Running: {cmd}\n")
    result = subprocess.run(cmd, shell=True, text=True)
    _save_history(args.task, cmd, executed=True)

    if result.returncode != 0:
        print(f"\n[!] Command exited with code {result.returncode}")


if __name__ == "__main__":
    main()
