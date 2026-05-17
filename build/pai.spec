# pai.spec — PyInstaller build specification for Linus PAI
# Build with:  pyinstaller build/pai.spec  (from the project root)
# Output:      dist/pai  (macOS/Linux)  or  dist/pai.exe  (Windows)

import sys
import platform
from pathlib import Path

ROOT = Path(SPECPATH).parent   # project root

# ── Platform-specific settings ────────────────────────────────────────────────
IS_MAC   = sys.platform == "darwin"
IS_WIN   = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
ARCH     = platform.machine().lower()

binary_name = "pai"

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    [str(ROOT / "build" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Bundle pai.py as a data file so the launcher can exec it
        (str(ROOT / "pai.py"), "."),
    ],
    hiddenimports=[
        # pai.py uses importlib.import_module for dynamic plugin loading
        "importlib.util",
        # Standard library modules imported at runtime
        "ast", "concurrent.futures", "email", "http.client",
        "http.server", "json", "logging", "platform", "queue",
        "shutil", "signal", "socket", "sqlite3", "ssl",
        "subprocess", "tarfile", "threading", "time",
        "traceback", "urllib.parse", "urllib.request",
        "urllib.error", "uuid", "zipfile",
        # Runtime imports inside pai.py functions
        "glob",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Heavy packages NOT pre-bundled (installed by pai.py --install at runtime)
        "mlx", "mlx_lm",
        "llama_cpp",
        "fastapi", "uvicorn", "starlette",
        "streamlit",
        "sentence_transformers", "torch", "transformers",
        "huggingface_hub",
        "rich",                 # loaded lazily; exclude to reduce binary size
        "pydantic",
        "numpy", "scipy",
        "PIL", "cv2",
        "matplotlib",
        "pandas",
        "sklearn",
        "tqdm",
    ],
    noarchive=False,
    optimize=2,                 # strip docstrings, optimise bytecode
)

# ── PYZ (bytecode archive) ────────────────────────────────────────────────────
pyz = PYZ(a.pure, a.zipped_data, cipher=None)

# ── One-file executable ───────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=binary_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,                    # strip debug symbols → smaller binary
    upx=False,                     # disabled by default; use --upx flag in build.sh to enable
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,                  # CLI tool — always console mode
    disable_windowed_traceback=False,
    argv_emulation=IS_MAC,         # macOS open-document support
    target_arch=None,              # None = native arch; set "universal2" for fat binary
    codesign_identity=None,        # set to Apple Developer ID for notarisation
    entitlements_file=None,
    icon=None,
)

# ── macOS .app bundle (optional — not needed for CLI) ────────────────────────
# Uncomment if you want a double-clickable .app:
# if IS_MAC:
#     app = BUNDLE(exe, name="LinusPAI.app", bundle_identifier="com.miryala3.linuspai")
