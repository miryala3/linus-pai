@echo off
:: ╔══════════════════════════════════════════════════════════════════════════╗
:: ║  Linus PAI — Windows self-contained launcher                            ║
:: ║  https://github.com/miryala3/linus-pai                                  ║
:: ║                                                                          ║
:: ║  No Python installation required. Double-click or run from cmd:          ║
:: ║    pai.cmd                                                               ║
:: ║    pai.cmd --chat                                                        ║
:: ║    pai.cmd --doctor                                                      ║
:: ║                                                                          ║
:: ║  First run downloads embedded Python 3.12 and compiles the GPU backend. ║
:: ╚══════════════════════════════════════════════════════════════════════════╝
setlocal enabledelayedexpansion

set PAI_VERSION=1.0.0
set PAI_PYTHON_VERSION=3.12.8
set PAI_PYTHON_RELEASE=20250115

if not defined PAI_HOME set PAI_HOME=%USERPROFILE%\.linus-pai
set PAI_PYTHON_DIR=%PAI_HOME%\python
set PAI_VENV_DIR=%PAI_HOME%\venv
set PAI_LOG=%PAI_HOME%\bootstrap.log
set PAI_SCRIPT_DIR=%~dp0
set PAI_SCRIPT=%PAI_SCRIPT_DIR%pai.py

if not exist "%PAI_HOME%" mkdir "%PAI_HOME%"

:: ── Banner ────────────────────────────────────────────────────────────────────
echo.
echo   LINUS PAI v%PAI_VERSION% -- Private AI Runtime
echo   github.com/miryala3/linus-pai
echo.

:: ── Version flag ──────────────────────────────────────────────────────────────
for %%a in (%*) do (
  if "%%a"=="--version" ( echo Linus PAI v%PAI_VERSION% ^(Python %PAI_PYTHON_VERSION%^) & exit /b 0 )
  if "%%a"=="-V"        ( echo Linus PAI v%PAI_VERSION% ^(Python %PAI_PYTHON_VERSION%^) & exit /b 0 )
)

:: ── Detect architecture ───────────────────────────────────────────────────────
set ARCH=x86_64
if "%PROCESSOR_ARCHITECTURE%"=="ARM64" set ARCH=aarch64

:: ── Check for embedded Python ─────────────────────────────────────────────────
set PY_BIN=%PAI_PYTHON_DIR%\python\python.exe
set VENV_PY=%PAI_VENV_DIR%\Scripts\python.exe

:: ── Find usable Python ────────────────────────────────────────────────────────
set PYTHON=
:: 1. Already bootstrapped venv
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import fastapi" >nul 2>&1
  if not errorlevel 1 ( set PYTHON=%VENV_PY% & goto :launch )
)
:: 2. Embedded python
if exist "%PY_BIN%" (
  "%PY_BIN%" -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
  if not errorlevel 1 ( set PYTHON=%PY_BIN% & goto :bootstrap )
)
:: 3. System Python 3.10+
for %%p in (python3.13 python3.12 python3.11 python3.10 python3 python) do (
  where %%p >nul 2>&1
  if not errorlevel 1 (
    %%p -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
    if not errorlevel 1 ( set PYTHON=%%p & goto :bootstrap )
  )
)

:: ── Download embedded Python ──────────────────────────────────────────────────
echo [PAI] No Python 3.10+ found -- downloading embedded Python %PAI_PYTHON_VERSION%...
set PY_TARBALL=cpython-%PAI_PYTHON_VERSION%+%PAI_PYTHON_RELEASE%-%ARCH%-pc-windows-msvc-shared-install_only.tar.gz
set PY_URL=https://github.com/indygreg/python-build-standalone/releases/download/%PAI_PYTHON_RELEASE%/%PY_TARBALL%
set PY_TMP=%PAI_HOME%\%PY_TARBALL%

echo [PAI] Downloading from: %PY_URL%

:: Use PowerShell to download
powershell -NoProfile -Command ^
  "[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; " ^
  "(New-Object System.Net.WebClient).DownloadFile('%PY_URL%', '%PY_TMP%')"
if errorlevel 1 (
  echo [ERR] Download failed. Check network connection.
  pause & exit /b 1
)

echo [PAI] Extracting Python runtime...
if not exist "%PAI_PYTHON_DIR%" mkdir "%PAI_PYTHON_DIR%"
powershell -NoProfile -Command ^
  "Expand-Archive -Force -LiteralPath '%PY_TMP%' -DestinationPath '%PAI_PYTHON_DIR%'"
if errorlevel 1 (
  echo [ERR] Extraction failed.
  pause & exit /b 1
)
del /f /q "%PY_TMP%" >nul 2>&1
set PYTHON=%PY_BIN%
echo [OK]  Embedded Python ready.

:bootstrap
:: ── Create venv and install deps (once) ──────────────────────────────────────
if exist "%VENV_PY%" (
  "%VENV_PY%" -c "import fastapi" >nul 2>&1
  if not errorlevel 1 goto :launch
)

echo [PAI] First-run setup (5-15 minutes, only once)...
echo [PAI] Log: %PAI_LOG%
echo.

"%PYTHON%" -m venv "%PAI_VENV_DIR%" >> "%PAI_LOG%" 2>&1
if errorlevel 1 ( echo [ERR] venv creation failed & pause & exit /b 1 )

"%VENV_PY%" -m pip install --upgrade pip wheel setuptools -q >> "%PAI_LOG%" 2>&1

echo [PAI] Installing dependencies and compiling GPU backend (CUDA / Vulkan)...
set PAI_DATA_DIR=%PAI_HOME%\data
"%VENV_PY%" "%PAI_SCRIPT%" --install 2>&1 | tee /a "%PAI_LOG%"
if errorlevel 1 (
  echo [ERR] Dependency install failed. See %PAI_LOG%
  pause & exit /b 1
)

echo.
echo [OK]  Linus PAI ready.
echo.

:launch
:: ── Launch pai.py with all arguments ─────────────────────────────────────────
set PAI_DATA_DIR=%PAI_HOME%\data
"%VENV_PY%" "%PAI_SCRIPT%" %*
endlocal
