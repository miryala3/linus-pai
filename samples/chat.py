#!/usr/bin/env python3
"""
chat.py — Feature-rich terminal chat with Linus PAI
Multi-turn conversation with memory, personas, file context, and web search.

Usage:
    python samples/chat.py                           # plain chat
    python samples/chat.py --persona coder           # coding assistant
    python samples/chat.py --persona analyst         # data analyst
    python samples/chat.py --file mycode.py          # chat about a file
    python samples/chat.py --web                     # enable live web search
    python samples/chat.py --export chat.md          # save conversation
    python samples/chat.py --model sudo              # use the large model
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PAI_API = os.getenv("PAI_API", "http://localhost:9480")

PERSONAS = {
    "default": "You are a helpful, honest, and concise AI assistant running locally.",
    "coder": (
        "You are a senior software engineer. You write clean, idiomatic, well-tested code. "
        "You always explain your reasoning. You prefer simple solutions over clever ones. "
        "You proactively mention edge cases, security issues, and performance implications."
    ),
    "analyst": (
        "You are a data analyst and scientist. You reason carefully about data, statistics, "
        "and causality. You distinguish correlation from causation. You prefer quantitative "
        "arguments and ask for data before drawing conclusions."
    ),
    "teacher": (
        "You are a patient teacher who explains complex topics using analogies, examples, "
        "and step-by-step reasoning. You check understanding and adjust depth to the student."
    ),
    "critic": (
        "You are a constructive critic. You rigorously stress-test ideas, find flaws, "
        "and ask hard questions. You are direct but not harsh. You help people think better."
    ),
    "writer": (
        "You are a skilled writer and editor. You help with essays, stories, emails, "
        "and documentation. You focus on clarity, voice, and audience-appropriate tone."
    ),
}


def _call_pai_chat(messages: list, model: str = "auto", web: bool = False) -> str:
    # Build a flat prompt from message history
    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    turns  = [m for m in messages if m["role"] != "system"]
    prompt = system + "\n\n" if system else ""
    for m in turns:
        tag = "User" if m["role"] == "user" else "Assistant"
        prompt += f"{tag}: {m['content']}\n\n"
    prompt += "Assistant:"

    payload = json.dumps({
        "prompt": prompt.strip(),
        "model": model, "max_tokens": 2048,
        "web_rag": web,
    }).encode()
    req = urllib.request.Request(
        f"{PAI_API}/generate", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())["response"]
    except Exception as exc:
        return f"[Error: {exc}]"


def _get_status() -> dict:
    try:
        with urllib.request.urlopen(f"{PAI_API}/status", timeout=3) as r:
            return json.loads(r.read())
    except Exception:
        return {}


COMMANDS = {
    "/help":    "Show this help",
    "/clear":   "Clear conversation history",
    "/model X": "Switch model (sudo/sized/auto)",
    "/persona X":"Switch persona (default/coder/analyst/teacher/critic/writer)",
    "/file X":  "Load a file into context",
    "/web":     "Toggle web search",
    "/save":    "Save conversation to file",
    "/status":  "Show PAI server status",
    "/quit":    "Exit",
}


def _print_help():
    print("\nCommands:")
    for cmd, desc in COMMANDS.items():
        print(f"  {cmd:20} {desc}")


RESET="\033[0m"; BOLD="\033[1m"; DIM="\033[2m"
CYAN="\033[36m"; GREEN="\033[32m"; YELLOW="\033[33m"


def _thinking():
    frames = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]
    i = 0
    import threading
    stop = threading.Event()
    def spin():
        while not stop.is_set():
            print(f"\r  {CYAN}{frames[i % len(frames)]}{RESET} Thinking…", end="", flush=True)
            i_ref[0] += 1
            time.sleep(0.08)
        print("\r" + " " * 20 + "\r", end="")
    i_ref = [0]
    t = threading.Thread(target=spin, daemon=True)
    t.start()
    return stop


def main():
    ap = argparse.ArgumentParser(description="PAI Terminal Chat")
    ap.add_argument("--persona", default="default",
                    choices=list(PERSONAS.keys()), help="Assistant persona")
    ap.add_argument("--file",    metavar="FILE",  help="File to load into context")
    ap.add_argument("--model",   default="auto",  help="Model: sudo | sized | auto")
    ap.add_argument("--web",     action="store_true", help="Enable web search")
    ap.add_argument("--export",  metavar="FILE",  help="Export conversation on exit")
    args = ap.parse_args()

    # Status check
    status = _get_status()
    if status:
        model_info = status.get("sudo_model", "?")
        thermal    = status.get("thermal", {}).get("state", "?")
        print(f"{CYAN}Linus PAI{RESET}  "
              f"model={model_info}  thermal={thermal}  "
              f"web={'on' if args.web else 'off'}  "
              f"persona={args.persona}")
    else:
        print(f"{YELLOW}[!] PAI server not reachable at {PAI_API} — start with runpai.sh{RESET}")
        sys.exit(1)

    print(f"{DIM}Type /help for commands.  Ctrl-C or /quit to exit.{RESET}\n")

    # Build initial system message
    system_msg = PERSONAS[args.persona]
    file_ctx   = ""
    if args.file:
        try:
            content = Path(args.file).read_text(errors="ignore")[:8000]
            file_ctx = f"\n\nContext file: {args.file}\n```\n{content}\n```"
        except Exception as exc:
            print(f"[!] Could not load file: {exc}")

    messages = [{"role": "system", "content": system_msg + file_ctx}]
    model    = args.model
    web      = args.web

    while True:
        try:
            user = input(f"{BOLD}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user:
            continue

        # Commands
        if user.startswith("/"):
            parts = user.split(maxsplit=1)
            cmd   = parts[0].lower()
            arg   = parts[1] if len(parts) > 1 else ""

            if cmd in ("/quit", "/exit", "/q"):
                break
            elif cmd == "/help":
                _print_help()
            elif cmd == "/clear":
                messages = [messages[0]]
                print(f"{DIM}[Conversation cleared]{RESET}")
            elif cmd == "/model":
                model = arg or "auto"
                print(f"{DIM}[Model: {model}]{RESET}")
            elif cmd == "/persona":
                if arg in PERSONAS:
                    messages[0] = {"role": "system", "content": PERSONAS[arg] + file_ctx}
                    print(f"{DIM}[Persona: {arg}]{RESET}")
                else:
                    print(f"Unknown persona. Options: {', '.join(PERSONAS.keys())}")
            elif cmd == "/file":
                try:
                    content  = Path(arg).read_text(errors="ignore")[:8000]
                    file_ctx = f"\n\nContext file: {arg}\n```\n{content}\n```"
                    messages[0]["content"] = PERSONAS[args.persona] + file_ctx
                    print(f"{DIM}[Loaded {arg}]{RESET}")
                except Exception as exc:
                    print(f"[!] {exc}")
            elif cmd == "/web":
                web = not web
                print(f"{DIM}[Web search: {'on' if web else 'off'}]{RESET}")
            elif cmd == "/save":
                fname = arg or "chat_export.md"
                lines = []
                for m in messages[1:]:
                    tag = "**You**" if m["role"] == "user" else "**PAI**"
                    lines.append(f"{tag}: {m['content']}\n")
                Path(fname).write_text("\n".join(lines))
                print(f"{DIM}[Saved to {fname}]{RESET}")
            elif cmd == "/status":
                s = _get_status()
                print(json.dumps(s, indent=2))
            continue

        messages.append({"role": "user", "content": user})

        stop = _thinking()
        try:
            reply = _call_pai_chat(messages, model=model, web=web)
        finally:
            stop.set()
            time.sleep(0.1)

        messages.append({"role": "assistant", "content": reply})
        print(f"\n{GREEN}PAI:{RESET} {reply}\n")

    if args.export:
        lines = []
        for m in messages[1:]:
            tag = "**You**" if m["role"] == "user" else "**PAI**"
            lines.append(f"{tag}: {m['content']}\n")
        Path(args.export).write_text("\n".join(lines))
        print(f"Conversation saved to {args.export}")

    print("Bye.")


if __name__ == "__main__":
    main()
