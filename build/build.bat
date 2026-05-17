@echo off
:: ══════════════════════════════════════════════════════════════════════════════
:: build/build.bat — Build the Linus PAI Windows native binary (pai.exe)
::
:: Creates: dist\pai.exe  — self-contained, no Python install required
::
:: Usage:
::   build\build.bat                 normal build
::   build\build.bat --clean         remove artefacts only
:: ══════════════════════════════════════════════════════════════════════════════
setlocal enabledelayedexpansion

set ROOT=%~dp0..
cd /d "%ROOT%"
set BUILD_VENV=%ROOT%\.build_venv
set BINARY=dist\pai.exe

for %%a in (%*) do (
  if "%%a"=="--clean" (
    echo [BUILD] Cleaning...
    if exist dist   rmdir /s /q dist
    if exist build\pai.build rmdir /s /q build\pai.build
    echo [OK]   Clean done.
    exit /b 0
  )
)

echo [BUILD] Linus PAI Native Binary Builder (Windows)
echo.

:: ── Find Python 3.10+ ─────────────────────────────────────────────────────────
set PYTHON=
for %%p in (python3.13 python3.12 python3.11 python3.10 python3 python) do (
  where %%p >nul 2>&1
  if not errorlevel 1 (
    %%p -c "import sys; exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
    if not errorlevel 1 ( set PYTHON=%%p & goto :py_found )
  )
)
echo [ERR] Python 3.10+ not found. Install from https://www.python.org/
exit /b 1

:py_found
echo [OK]  Python: %PYTHON%
for /f "tokens=*" %%v in ('%PYTHON% --version') do echo [OK]  Version: %%v

:: ── Build venv ────────────────────────────────────────────────────────────────
if not exist "%BUILD_VENV%\Scripts\python.exe" (
  echo [BUILD] Creating build venv...
  %PYTHON% -m venv "%BUILD_VENV%"
)

set PY=%BUILD_VENV%\Scripts\python.exe
set PIP=%BUILD_VENV%\Scripts\pip.exe

%PIP% install --upgrade pip -q
echo [BUILD] Installing PyInstaller...
%PIP% install pyinstaller -q

:: ── Build ─────────────────────────────────────────────────────────────────────
echo [BUILD] Building dist\pai.exe...
if exist dist rmdir /s /q dist

"%BUILD_VENV%\Scripts\pyinstaller.exe" build\pai.spec ^
  --distpath dist ^
  --workpath build\pai.build ^
  --noconfirm ^
  --log-level WARN ^
  --noupx

if not exist "%BINARY%" (
  echo [ERR] Build failed -- %BINARY% not found.
  exit /b 1
)

:: File size
for %%f in ("%BINARY%") do set SIZE=%%~zf
set /a SIZE_MB=%SIZE%/1048576
echo [OK]  Built: %BINARY%  (%SIZE_MB% MB)

:: ── Smoke test ────────────────────────────────────────────────────────────────
echo [BUILD] Smoke test...
"%BINARY%" --version
if errorlevel 1 (
  echo [WARN]  Smoke test failed -- check binary manually.
) else (
  echo [OK]   Smoke test passed.
)

echo.
echo [OK]  Binary: %ROOT%\dist\pai.exe
echo.
echo   Run:     dist\pai.exe
echo   Install: copy dist\pai.exe C:\Windows\System32\pai.exe
echo   Share:   Upload dist\pai.exe to GitHub Releases
echo.
endlocal
