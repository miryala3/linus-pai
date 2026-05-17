"""
launcher.py — Linus PAI native binary entry point
Compiled by PyInstaller into dist/pai (macOS/Linux) or dist/pai.exe (Windows).

Design principles:
  • pai.py is copied to PAI_HOME/pai.py on first launch so it is always
    accessible from a stable path — not from sys._MEIPASS which may be
    cleaned up in edge cases.
  • Every step has explicit error handling with a clear recovery suggestion.
  • Progress is printed to stdout so users see activity during long operations.
  • Uses subprocess.run instead of os.execve so the parent process (and
    therefore sys._MEIPASS) stays alive for the full duration.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PAI_VERSION        = "1.0.0"
PAI_PYTHON_VERSION = "3.12.8"
PAI_HOME           = Path(os.getenv("PAI_HOME", Path.home() / ".linus-pai"))
PAI_VENV           = PAI_HOME / "venv"
PAI_PAI_PY         = PAI_HOME / "pai.py"          # stable copy — never _MEIPASS
PAI_DATA_DIR       = PAI_HOME / "data"
PAI_LOG            = PAI_HOME / "bootstrap.log"   # errors/warnings — append-only
PAI_INSTALL_LOG    = PAI_HOME / "install.log"     # full install output — overwritten each run


# ── Console helpers ───────────────────────────────────────────────────────────

def _print(msg: str, end: str = "\n") -> None:
    print(msg, end=end, flush=True)

def _ok(msg: str)   -> None: _print(f"  \033[32m✓\033[0m  {msg}")
def _info(msg: str) -> None: _print(f"  \033[36m→\033[0m  {msg}")
def _warn(msg: str) -> None: _print(f"  \033[33m!\033[0m  {msg}")
def _err(msg: str)  -> None: _print(f"  \033[31m✘\033[0m  {msg}")

def _step(n: int, total: int, msg: str) -> None:
    bar = "█" * n + "░" * (total - n)
    _print(f"\r  [{bar}] {n}/{total}  {msg:<55}", end="")
    if n == total:
        _print("")


def _banner() -> None:
    _print(f"""
  ██╗     ██╗███╗   ██╗██╗   ██╗███████╗    ██████╗  █████╗ ██╗
  ██║     ██║████╗  ██║██║   ██║██╔════╝    ██╔══██╗██╔══██╗██║
  ██║     ██║██╔██╗ ██║██║   ██║███████╗    ██████╔╝███████║██║
  ██║     ██║██║╚██╗██║██║   ██║╚════██║    ██╔═══╝ ██╔══██║██║
  ███████╗██║██║ ╚████║╚██████╔╝███████║    ██║     ██║  ██║██║
  ╚══════╝╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚══════╝    ╚═╝     ╚═╝  ╚═╝╚═╝
  Private AI Runtime v{PAI_VERSION}  ·  github.com/miryala3/linus-pai
""")


# ── Locate pai.py (bundled in PyInstaller or on disk) ────────────────────────

def _bundled_pai_py() -> Path | None:
    """Return path to pai.py inside sys._MEIPASS (PyInstaller bundle)."""
    if hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / "pai.py"
        if p.exists():
            return p
    # Running from source in development
    for rel in ("../pai.py", "pai.py"):
        p = (Path(__file__).parent / rel).resolve()
        if p.exists():
            return p
    return None


def _ensure_pai_py() -> Path:
    """
    Return a stable path to pai.py at PAI_HOME/pai.py.
    Copies from bundle on first run or when the version has changed.
    """
    bundled = _bundled_pai_py()
    if bundled is None:
        if PAI_PAI_PY.exists():
            return PAI_PAI_PY      # use previously copied version
        _err("pai.py not found in bundle or on disk.")
        _err("Re-install: curl -Lo pai https://github.com/miryala3/linus-pai/releases/latest/download/pai-macos-arm64")
        sys.exit(1)

    # Copy to stable location if missing or outdated
    needs_copy = (
        not PAI_PAI_PY.exists()
        or PAI_PAI_PY.stat().st_size != bundled.stat().st_size
    )
    if needs_copy:
        PAI_HOME.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled, PAI_PAI_PY)

    return PAI_PAI_PY


# ── System Python finder ──────────────────────────────────────────────────────

def _find_system_python() -> str | None:
    """Return a real Python 3.10+ on PATH.

    sys.executable inside a PyInstaller one-file binary points to the frozen
    bundle itself, not to a real Python interpreter, so it cannot be used for
    'python -m venv'.  We need a genuine Python binary from the host system.
    """
    candidates = (
        "python3.12", "python3.11", "python3.10", "python3.13", "python3", "python",
    )
    for candidate in candidates:
        p = shutil.which(candidate)
        if not p:
            continue
        try:
            r = subprocess.run(
                [p, "-c", "import sys; exit(0 if sys.version_info >= (3, 10) else 1)"],
                capture_output=True, timeout=5,
            )
            if r.returncode == 0:
                return p
        except Exception:
            continue
    return None


# ── Venv helpers ──────────────────────────────────────────────────────────────

def _venv_python() -> Path:
    if sys.platform == "win32":
        return PAI_VENV / "Scripts" / "python.exe"
    return PAI_VENV / "bin" / "python3"


def _venv_ready() -> bool:
    """True if venv exists and core packages import successfully."""
    py = _venv_python()
    if not py.exists():
        return False
    try:
        r = subprocess.run(
            [str(py), "-c", "import fastapi, uvicorn, rich, psutil"],
            capture_output=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def _venv_corrupt() -> bool:
    """True if venv dir exists but the Python binary is missing or broken."""
    py = _venv_python()
    if not PAI_VENV.exists():
        return False
    if not py.exists():
        return True
    try:
        r = subprocess.run([str(py), "--version"], capture_output=True, timeout=5)
        return r.returncode != 0
    except Exception:
        return True


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def _bootstrap(pai_py: Path) -> None:
    """Create venv and install all deps.  Shows step-by-step progress."""
    PAI_HOME.mkdir(parents=True, exist_ok=True)
    PAI_DATA_DIR.mkdir(parents=True, exist_ok=True)

    import datetime as _dt
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # install.log  — overwritten each bootstrap so it only shows the last run
    # bootstrap.log — append-only; records timestamps and errors only (no banner spam)
    with open(PAI_INSTALL_LOG, "w") as ilog:
        ilog.write(f"===== Linus PAI install  {ts} =====\n")
        ilog.write(f"Python: {sys.executable}\n\n")
    with open(PAI_LOG, "a") as blog:
        blog.write(f"[{ts}] Bootstrap started\n")

    _banner()
    _info("First-run setup — this takes 5–15 minutes once, then instant.")
    _info(f"Install log: {PAI_INSTALL_LOG}")
    _print("")

    # ── Detect if we need to remove a corrupt venv ─────────────────────────────
    if _venv_corrupt():
        _warn("Existing venv is corrupt — removing and rebuilding…")
        try:
            shutil.rmtree(PAI_VENV, ignore_errors=True)
            _ok("Corrupt venv removed.")
        except Exception as exc:
            _err(f"Could not remove corrupt venv: {exc}")
            _err(f"Manually run: rm -rf {PAI_VENV}")
            sys.exit(1)

    total = 4

    # sys.executable in a PyInstaller one-file binary is the frozen bundle, not
    # a real Python interpreter.  'frozen-exe -m venv' fails immediately.
    # Find a genuine system Python to create the venv.
    sys_python = _find_system_python()
    if sys_python is None:
        _err("No Python 3.10+ found on this system.")
        _err("Install Python 3.12 from https://www.python.org/ then re-run.")
        with open(PAI_LOG, "a") as blog:
            blog.write(f"[{ts}] ERROR: no system Python found\n")
        sys.exit(1)

    with open(PAI_INSTALL_LOG, "a") as ilog:
        ilog.write(f"System Python: {sys_python}\n\n")

    # Step 1 — Create venv (errors → install log only)
    _step(1, total, "Creating virtual environment…")
    try:
        with open(PAI_INSTALL_LOG, "a") as ilog:
            r = subprocess.run(
                [sys_python, "-m", "venv", str(PAI_VENV)],
                stdout=ilog, stderr=ilog,
            )
    except KeyboardInterrupt:
        _print("\n")
        _warn("Interrupted during venv creation.  Re-run to retry.")
        sys.exit(130)
    if r.returncode != 0:
        _print("")
        _err("venv creation failed.")
        _err(f"See log: {PAI_INSTALL_LOG}")
        _err("Recovery: check available disk space (need ≥ 2 GB) and Python version.")
        with open(PAI_LOG, "a") as blog:
            blog.write(f"[{ts}] ERROR: venv creation failed\n")
        sys.exit(1)
    _ok("Virtual environment created.")

    py = str(_venv_python())

    # Step 2 — Upgrade pip (silent, errors → install log)
    _step(2, total, "Upgrading pip…")
    try:
        with open(PAI_INSTALL_LOG, "a") as ilog:
            subprocess.run(
                [py, "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools", "-q"],
                check=False, stdout=ilog, stderr=ilog,
            )
    except KeyboardInterrupt:
        _print("\n")
        _warn("Interrupted during pip upgrade.  Re-run to retry.")
        sys.exit(130)

    # Step 3 — Install deps
    # stdout → terminal (user sees live progress bars from pai.py)
    # stderr → install log (pip warnings, compile output — not repeated in bootstrap.log)
    _step(3, total, "Installing dependencies and compiling GPU backend…")
    _print("")
    env = {**os.environ, "PAI_DATA_DIR": str(PAI_DATA_DIR)}
    try:
        with open(PAI_INSTALL_LOG, "a") as ilog:
            r = subprocess.run(
                [py, str(pai_py), "--install"],
                env=env,
                stdout=None,    # live progress to terminal
                stderr=ilog,    # warnings/errors to install log only
            )
    except KeyboardInterrupt:
        _print("\n")
        _warn("Installation interrupted.  Re-run to resume (already-downloaded packages are kept).")
        sys.exit(130)
    if r.returncode != 0:
        _print("")
        _err("Dependency install failed.")
        _err(f"See log: {PAI_INSTALL_LOG}")
        _err("Recovery options:")
        _err("  1. Re-run: ./pai --force-install")
        _err("  2. Manual: pip install -r requirements.txt")
        _err("  3. Check network and disk space, then: ./pai --doctor")
        with open(PAI_LOG, "a") as blog:
            blog.write(f"[{ts}] ERROR: dependency install failed\n")
        sys.exit(1)

    # Step 4 — Verify
    _step(4, total, "Verifying installation…")
    if not _venv_ready():
        _print("")
        _warn("Some packages may not have installed correctly.")
        _warn("Run ./pai --doctor for a detailed check.")
    else:
        _ok("All packages verified.")

    with open(PAI_LOG, "a") as blog:
        blog.write(f"[{ts}] Bootstrap succeeded\n")
    _print("")
    _ok(f"Linus PAI is ready!  Run: ./pai --chat")
    _ok(f"Install log: {PAI_INSTALL_LOG}")
    _print("")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = sys.argv[1:]

    if args and args[0] in ("--version", "-V"):
        _print(f"Linus PAI v{PAI_VERSION} (Python {PAI_PYTHON_VERSION})")
        return

    # Locate and copy pai.py to a stable path on every invocation
    try:
        pai_py = _ensure_pai_py()
    except SystemExit:
        raise
    except Exception as exc:
        _err(f"Could not locate pai.py: {exc}")
        sys.exit(1)

    # Bootstrap if venv is missing or corrupt
    if not _venv_ready():
        if _venv_corrupt():
            _warn("Venv is corrupt — rebuilding…")
        try:
            _bootstrap(pai_py)
        except KeyboardInterrupt:
            _print("")
            _warn("Bootstrap interrupted.  Re-run to resume.")
            sys.exit(130)

        # Recheck after bootstrap
        if not _venv_ready():
            _err("Bootstrap completed but venv is still not ready.")
            _err(f"See install log: {PAI_INSTALL_LOG}")
            _err("Run: ./pai --doctor")
            sys.exit(1)

    py  = str(_venv_python())
    env = {**os.environ, "PAI_DATA_DIR": str(PAI_DATA_DIR)}

    # Launch pai.py via the venv Python.
    # subprocess.run keeps this process alive (and sys._MEIPASS accessible)
    # for the full duration instead of replacing it with os.execve.
    try:
        result = subprocess.run([py, str(pai_py)] + args, env=env)
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        sys.exit(0)
    except FileNotFoundError:
        _err(f"Python not found at: {py}")
        _err("Venv may be corrupt.  Run: ./pai --force-install")
        sys.exit(1)
    except Exception as exc:
        _err(f"Launch failed: {exc}")
        _err("Run: ./pai --doctor")
        sys.exit(1)


if __name__ == "__main__":
    main()
