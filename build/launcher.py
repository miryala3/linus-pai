"""
launcher.py — Linus PAI native binary entry point
Compiled by PyInstaller into dist/pai (macOS/Linux) or dist/pai.exe (Windows).

This thin launcher:
  1. Locates pai.py (bundled as a data file in the PyInstaller package)
  2. Bootstraps a venv in ~/.linus-pai/venv on first run
  3. Re-executes pai.py via the venv's Python with all original arguments

Why a two-stage launch?
  llama-cpp-python and mlx need to be compiled for the specific GPU at runtime.
  We cannot pre-bundle GPU-specific native extensions. The venv is built once
  by pai.py --install and reused on every subsequent run (< 1 second startup).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

PAI_VERSION      = "1.0.0"
PAI_PYTHON_VERSION = "3.12.8"
PAI_HOME         = Path(os.getenv("PAI_HOME", Path.home() / ".linus-pai"))
PAI_VENV         = PAI_HOME / "venv"
PAI_LOG          = PAI_HOME / "bootstrap.log"
PAI_DATA_DIR     = PAI_HOME / "data"

# ── Locate pai.py (bundled data file or next to this script) ──────────────────

def _find_pai_py() -> Path:
    # PyInstaller bundles data files into sys._MEIPASS at runtime
    if hasattr(sys, "_MEIPASS"):
        candidate = Path(sys._MEIPASS) / "pai.py"
        if candidate.exists():
            return candidate

    # Running from source (development mode)
    here = Path(__file__).parent
    for rel in ("../pai.py", "pai.py"):
        candidate = (here / rel).resolve()
        if candidate.exists():
            return candidate

    raise FileNotFoundError(
        "pai.py not found. Expected next to the binary or bundled as a data file.\n"
        "Re-install: https://github.com/miryala3/linus-pai"
    )


# ── Find the venv Python ──────────────────────────────────────────────────────

def _venv_python() -> Path:
    if sys.platform == "win32":
        return PAI_VENV / "Scripts" / "python.exe"
    return PAI_VENV / "bin" / "python3"


def _venv_ready() -> bool:
    py = _venv_python()
    if not py.exists():
        return False
    try:
        result = subprocess.run(
            [str(py), "-c", "import fastapi, uvicorn, rich, psutil"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


# ── Bootstrap (first run only) ────────────────────────────────────────────────

def _bootstrap(pai_py: Path) -> None:
    PAI_HOME.mkdir(parents=True, exist_ok=True)
    PAI_DATA_DIR.mkdir(parents=True, exist_ok=True)

    _banner()
    print("[PAI] First-run setup (5–15 minutes, only once)")
    print(f"[PAI] Log: {PAI_LOG}")
    print()

    sys_python = sys.executable   # the embedded Python inside the PyInstaller binary

    # Create venv
    print("[PAI] Creating virtual environment…", flush=True)
    with open(PAI_LOG, "a") as log:
        result = subprocess.run(
            [sys_python, "-m", "venv", str(PAI_VENV)],
            stdout=log, stderr=log,
        )
    if result.returncode != 0:
        sys.exit(f"[ERR] venv creation failed. See {PAI_LOG}")

    py = str(_venv_python())

    # Upgrade pip
    subprocess.run([py, "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools", "-q"],
                   check=False)

    # Run pai.py --install (compiles GPU backend, installs all packages)
    print("[PAI] Installing dependencies and compiling GPU backend…", flush=True)
    env = {**os.environ, "PAI_DATA_DIR": str(PAI_DATA_DIR)}
    result = subprocess.run([py, str(pai_py), "--install"], env=env)
    if result.returncode != 0:
        sys.exit(f"[ERR] Install failed. See {PAI_LOG}")

    print()
    print("[OK]  Linus PAI ready.")
    print()


# ── Banner ────────────────────────────────────────────────────────────────────

def _banner() -> None:
    print(f"""
  ██╗     ██╗███╗   ██╗██╗   ██╗███████╗    ██████╗  █████╗ ██╗
  ██║     ██║████╗  ██║██║   ██║██╔════╝    ██╔══██╗██╔══██╗██║
  ██║     ██║██╔██╗ ██║██║   ██║███████╗    ██████╔╝███████║██║
  ██║     ██║██║╚██╗██║██║   ██║╚════██║    ██╔═══╝ ██╔══██║██║
  ███████╗██║██║ ╚████║╚██████╔╝███████║    ██║     ██║  ██║██║
  ╚══════╝╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚══════╝    ╚═╝     ╚═╝  ╚═╝╚═╝
  Private AI Runtime v{PAI_VERSION}  ·  github.com/miryala3/linus-pai
""")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if args and args[0] in ("--version", "-V"):
        print(f"Linus PAI v{PAI_VERSION} (Python {PAI_PYTHON_VERSION})")
        return

    try:
        pai_py = _find_pai_py()
    except FileNotFoundError as exc:
        sys.exit(str(exc))

    # Bootstrap venv on first run
    if not _venv_ready():
        _bootstrap(pai_py)

    # Hand off to the venv's Python with pai.py + all original arguments.
    # We exec() so this process is replaced — no zombie parent.
    py  = str(_venv_python())
    env = {**os.environ, "PAI_DATA_DIR": str(PAI_DATA_DIR)}

    if sys.platform == "win32":
        # Windows has no exec() — use subprocess
        result = subprocess.run([py, str(pai_py)] + args, env=env)
        sys.exit(result.returncode)
    else:
        os.execve(py, [py, str(pai_py)] + args, env)   # replaces current process


if __name__ == "__main__":
    main()
