#!/usr/bin/env python3
"""
agentic_task.py — Fully autonomous multi-step task runner
Give PAI a complex objective; it plans, executes tools, monitors results,
and iterates until the task is complete — all locally, no cloud needed.

Usage:
    python samples/agentic_task.py "Create a REST API for a todo list with SQLite, write tests, and verify they pass"
    python samples/agentic_task.py "Analyse all .py files in src/ and produce a refactoring report"
    python samples/agentic_task.py "Search for the 5 latest papers on mixture-of-experts and summarise each"
    python samples/agentic_task.py --interactive    # REPL for multi-step sessions
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

TASK_COLORS = {
    "plan":    "\033[36m",  # cyan
    "act":     "\033[33m",  # yellow
    "observe": "\033[35m",  # magenta
    "answer":  "\033[32m",  # green
    "error":   "\033[31m",  # red
}
RESET = "\033[0m"


def _call_agent(task: str, web: bool = True) -> dict:
    payload = json.dumps({
        "task": task, "web_rag": web, "code_mode": False,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/agent", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except Exception as exc:
        return {"answer": f"[Agent error: {exc}]"}


def _call_code_agent(task: str) -> dict:
    payload = json.dumps({
        "task": task, "web_rag": False, "code_mode": True,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/agent", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())
    except Exception as exc:
        return {"answer": f"[Code agent error: {exc}]"}


def _stream_print(text: str, width: int = 80) -> None:
    """Print text with word-wrap, simulating streaming output."""
    words = text.split()
    line  = ""
    for word in words:
        if len(line) + len(word) + 1 > width:
            print(line)
            line = word
        else:
            line = (line + " " + word).strip()
    if line:
        print(line)


def _is_code_task(task: str) -> bool:
    code_signals = [
        "write code", "create a", "implement", "build a",
        "fix the", "refactor", "add tests", "make the tests pass",
        "generate a script", "write a function",
    ]
    task_lower = task.lower()
    return any(s in task_lower for s in code_signals)


def run_task(task: str, verbose: bool = True) -> str:
    """Run a task through the PAI agent and return the final answer."""
    if verbose:
        print(f"\n{'='*60}")
        print(f"{TASK_COLORS['plan']}Task:{RESET} {task}")
        print(f"{'='*60}")

    t0 = time.time()
    use_code = _is_code_task(task)

    if verbose:
        mode = "Code Agent" if use_code else "ReAct Agent"
        print(f"{TASK_COLORS['act']}Mode:{RESET} {mode}  |  Running…\n")

    # Show spinner
    spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = [0]
    import threading
    stop_spin = threading.Event()
    def spin():
        while not stop_spin.is_set():
            print(f"\r  {spinner_chars[i[0] % len(spinner_chars)]} Processing…", end="", flush=True)
            i[0] += 1
            time.sleep(0.1)
        print("\r" + " " * 25 + "\r", end="")
    t = threading.Thread(target=spin, daemon=True)
    t.start()

    try:
        if use_code:
            result = _call_code_agent(task)
        else:
            result = _call_agent(task)
    finally:
        stop_spin.set()
        time.sleep(0.15)

    answer = result.get("answer", "[No answer returned]")
    elapsed = time.time() - t0

    if verbose:
        print(f"{TASK_COLORS['answer']}Answer:{RESET} ({elapsed:.1f}s)\n")
        _stream_print(answer)

    return answer


def run_pipeline(tasks: list, stop_on_error: bool = True) -> list:
    """Run a sequence of tasks, optionally injecting results into later tasks."""
    results = []
    context = {}

    for i, task in enumerate(tasks, 1):
        # Allow {prev_result} or {result_N} templating
        task_filled = task.format(**context, prev_result=results[-1] if results else "")
        print(f"\n[Step {i}/{len(tasks)}]")
        answer = run_task(task_filled)
        results.append(answer)
        context[f"result_{i}"] = answer

        if "[error" in answer.lower() and stop_on_error:
            print(f"\n{TASK_COLORS['error']}Pipeline stopped at step {i} due to error.{RESET}")
            break

    return results


def interactive_session() -> None:
    print("Linus PAI — Agentic Task Runner (interactive)")
    print("Commands: /code (code agent) | /pipe (pipeline) | /quit")
    print("Just type a task to run it.\n")

    while True:
        try:
            line = input("Task> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not line:
            continue
        if line.lower() in ("/quit", "/exit", "quit"):
            break

        if line.lower() == "/pipe":
            print("Enter tasks one per line (empty line to run):")
            tasks = []
            while True:
                try:
                    t = input(f"  {len(tasks)+1}> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not t:
                    break
                tasks.append(t)
            if tasks:
                run_pipeline(tasks)
        elif line.lower() == "/code":
            task = input("Code task> ").strip()
            if task:
                result = _call_code_agent(task)
                _stream_print(result.get("answer", ""))
        else:
            run_task(line)


def main():
    ap = argparse.ArgumentParser(description="PAI Agentic Task Runner")
    ap.add_argument("task",         nargs="?",      help="Task description")
    ap.add_argument("--interactive",action="store_true", help="Interactive REPL session")
    ap.add_argument("--pipeline",   metavar="FILE", help="JSON file with list of tasks")
    ap.add_argument("--out",        metavar="FILE", help="Save final answer to file")
    ap.add_argument("--quiet",      action="store_true", help="Print only the final answer")
    args = ap.parse_args()

    if args.interactive:
        interactive_session()
        return

    if args.pipeline:
        tasks = json.loads(Path(args.pipeline).read_text())
        results = run_pipeline(tasks)
        if args.out:
            Path(args.out).write_text("\n\n---\n\n".join(results))
        return

    if not args.task:
        ap.print_help()
        return

    answer = run_task(args.task, verbose=not args.quiet)

    if args.quiet:
        print(answer)

    if args.out:
        Path(args.out).write_text(answer)
        print(f"\nSaved to {args.out}")


if __name__ == "__main__":
    main()
