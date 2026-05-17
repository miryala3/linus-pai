@echo off
:: ==============================================================================
:: runpai.bat — AIO Linus PAI Local AI Runtime Launcher (Windows)
:: Installs all prerequisites, sets up venv, and starts API + Streamlit UI.
::
:: Usage (double-click or from cmd):
::   runpai.bat                   auto (API + UI)
::   runpai.bat --chat            terminal chat
::   runpai.bat --serve           API only
::   runpai.bat --status          device/model status
::   runpai.bat --install         deps only
::   runpai.bat --agent "task"    one-shot agent
::   runpai.bat --code  "task"    one-shot code agent
::   runpai.bat --port  9480      override API port
::   runpai.bat --ui-port 8501    override UI port
::   runpai.bat --force-install   re-run bootstrap
:: ==============================================================================

setlocal enabledelayedexpansion

:: ── Paths ────────────────────────────────────────────────────────────────────
set "AIO_DIR=%~dp0"
set "AIO_VENV=%AIO_DIR%.venv"
set "AIO_PID_DIR=%USERPROFILE%\.aio"
set "AIO_PID_FILE=%AIO_PID_DIR%\aio.pids"
set "AIO_LOG_DIR=%AIO_PID_DIR%\logs"
set "API_PORT=9480"
set "UI_PORT=8501"

if not exist "%AIO_PID_DIR%" mkdir "%AIO_PID_DIR%"
if not exist "%AIO_LOG_DIR%" mkdir "%AIO_LOG_DIR%"

:: ── Banner ───────────────────────────────────────────────────────────────────
echo.
echo   ___   ___ ___
echo  ^|   ^| ^|_ _^|  ^|  Linus PAI Local AI Runtime
echo  ^| ^|^| ^| ^| ^|^|  ^|  Windows Launcher
echo  ^|___^| ^|___^|  ^|
echo.

:: ── Already running? ─────────────────────────────────────────────────────────
if exist "%AIO_PID_FILE%" (
    echo [WARN] AIO may already be running. Check %AIO_PID_FILE%
    echo        Run stoppai.bat first to stop it cleanly.
    echo.
)

:: ── Parse --port and --ui-port ───────────────────────────────────────────────
:parse_args
if "%~1"=="--port"     ( set "API_PORT=%~2" & shift & shift & goto parse_args )
if "%~1"=="--ui-port"  ( set "UI_PORT=%~2"  & shift & shift & goto parse_args )

:: ── Check Python ─────────────────────────────────────────────────────────────
echo [PAI] Checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo [PAI] Python not found. Attempting to install via winget...
    winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo [ERR] Could not install Python automatically.
        echo       Please install Python 3.10+ from https://www.python.org/downloads/
        echo       Make sure to check "Add Python to PATH" during installation.
        pause
        exit /b 1
    )
    :: Refresh PATH
    call RefreshEnv.cmd 2>nul || (
        echo [WARN] Please close and reopen this window, then run runpai.bat again.
        pause
        exit /b 0
    )
)

:: Verify version
for /f "tokens=2" %%v in ('python --version 2^>^&1') do set "PY_VER=%%v"
echo [OK]  Python %PY_VER%

:: ── Check Git ─────────────────────────────────────────────────────────────────
git --version >nul 2>&1
if errorlevel 1 (
    echo [PAI] Installing Git via winget...
    winget install --id Git.Git --silent --accept-package-agreements --accept-source-agreements 2>nul || (
        echo [WARN] Git not found. Some features may not work.
    )
)

:: ── Check CMake (needed to compile llama-cpp-python) ─────────────────────────
cmake --version >nul 2>&1
if errorlevel 1 (
    echo [PAI] Installing CMake via winget...
    winget install --id Kitware.CMake --silent --accept-package-agreements --accept-source-agreements 2>nul || (
        echo [WARN] CMake not found. llama-cpp-python may fail to build.
        echo        Install from https://cmake.org/download/
    )
)

:: ── Check Visual Studio Build Tools ──────────────────────────────────────────
cl >nul 2>&1
if errorlevel 1 (
    echo [WARN] MSVC compiler not found. llama-cpp-python needs Visual Studio Build Tools.
    echo        Download: https://aka.ms/vs/17/release/vs_BuildTools.exe
    echo        Select "Desktop development with C++" workload.
    echo        Press any key to continue anyway (pre-built wheel may still work)...
    pause >nul
)

:: ── Check CUDA ────────────────────────────────────────────────────────────────
nvidia-smi >nul 2>&1
if not errorlevel 1 (
    echo [OK]  NVIDIA GPU detected
    set "CMAKE_ARGS=-DGGML_CUDA=on"
) else (
    echo [INFO] No NVIDIA GPU detected — using CPU inference
    set "CMAKE_ARGS="
)

:: ── Virtual environment ───────────────────────────────────────────────────────
if not exist "%AIO_VENV%\Scripts\python.exe" (
    echo [PAI] Creating virtual environment...
    python -m venv "%AIO_VENV%"
    if errorlevel 1 (
        echo [ERR] Failed to create venv
        pause
        exit /b 1
    )
)

echo [OK]  venv: %AIO_VENV%
call "%AIO_VENV%\Scripts\activate.bat"

:: Upgrade pip
python -m pip install --upgrade pip wheel setuptools -q

:: ── Install AIO dependencies ──────────────────────────────────────────────────
echo [PAI] Installing dependencies (first run may take 10-20 minutes)...
set "LOG_FILE=%AIO_LOG_DIR%\install.log"
python "%AIO_DIR%pai.py" --install >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo [ERR] Dependency installation failed. See %LOG_FILE%
    pause
    exit /b 1
)
echo [OK]  Dependencies installed.

:: ── Short-circuit modes ───────────────────────────────────────────────────────
for %%a in (%*) do (
    if "%%a"=="--install"  goto :done_install
    if "%%a"=="--status"   goto :run_passthrough
    if "%%a"=="--train"    goto :run_passthrough
    if "%%a"=="--chat"     goto :run_passthrough
    if "%%a"=="--agent"    goto :run_passthrough
    if "%%a"=="--code"     goto :run_passthrough
)
goto :launch_server

:done_install
echo [OK] Install complete.
goto :end

:run_passthrough
echo [PAI] Running: python pai.py %*
python "%AIO_DIR%pai.py" %*
goto :end

:: ── Launch server ─────────────────────────────────────────────────────────────
:launch_server
set "LOG_FILE=%AIO_LOG_DIR%\aio_%date:~-4,4%%date:~-7,2%%date:~-10,2%_%time:~0,2%%time:~3,2%.log"

echo [PAI] Launching server in background...
echo [PAI] Logs → %LOG_FILE%

start "PAI Server" /min cmd /c "call "%AIO_VENV%\Scripts\activate.bat" && python "%AIO_DIR%pai.py" %* >> "%LOG_FILE%" 2>&1"

:: Wait for API
echo [PAI] Waiting for API on port %API_PORT%...
set /a tries=0
:wait_loop
timeout /t 2 /nobreak >nul
curl -sf "http://localhost:%API_PORT%/status" >nul 2>&1
if not errorlevel 1 goto :api_ready
set /a tries+=1
if %tries% lss 30 goto :wait_loop
echo [WARN] API not ready after 60s — check %LOG_FILE%
goto :open_browser

:api_ready
echo [OK]  API ready!

:open_browser
echo.
echo [PAI] UI  → http://localhost:%UI_PORT%
echo [PAI] API → http://localhost:%API_PORT%/docs
echo.

timeout /t 3 /nobreak >nul
start "" "http://localhost:%UI_PORT%"

echo [OK] AIO is running.
echo      Run stoppai.bat to stop.
echo.

:end
endlocal
