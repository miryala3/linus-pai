@echo off
:: ==============================================================================
:: stopaio.bat — Stop the PAI All-In-One Local AI Runtime (Windows)
:: Reads %USERPROFILE%\.aio\aio.pids and terminates recorded processes.
:: Fallback: taskkill by process/window name.
:: ==============================================================================

setlocal enabledelayedexpansion

set "PAI_PID_FILE=%USERPROFILE%\.aio\aio.pids"
set killed=0

echo.
echo PAI -- Stopping All Services
echo ==============================

:: ── Kill by PID file ──────────────────────────────────────────────────────────
if exist "%PAI_PID_FILE%" (
    for /f "tokens=*" %%p in (%PAI_PID_FILE%) do (
        set "pid=%%p"
        if not "!pid!"=="" (
            taskkill /PID !pid! /T /F >nul 2>&1
            if not errorlevel 1 (
                echo [OK]  Terminated PID !pid!
                set /a killed+=1
            ) else (
                echo [WARN] PID !pid! not found or already stopped
            )
        )
    )
    del /f /q "%PAI_PID_FILE%" >nul 2>&1
) else (
    echo [WARN] No PID file found at %PAI_PID_FILE%
)

:: ── Fallback: kill by process name ───────────────────────────────────────────
taskkill /IM python.exe /F /FI "WINDOWTITLE eq PAI Server" >nul 2>&1
if not errorlevel 1 (
    echo [OK]  Terminated PAI Server window processes
    set /a killed+=1
)

:: Kill any streamlit processes linked to pai_frontend
for /f "tokens=2" %%p in ('tasklist /fi "imagename eq python.exe" /fo table /nh 2^>nul') do (
    wmic process where "ProcessId=%%p" get CommandLine /format:value 2>nul | findstr /i "pai_frontend" >nul
    if not errorlevel 1 (
        taskkill /PID %%p /F /T >nul 2>&1
        echo [OK]  Terminated pai_frontend process PID %%p
        set /a killed+=1
    )
)

:: ── Port cleanup: release 9480 and 8501 ──────────────────────────────────────
for %%port in (9480 8501 9777 9479) do (
    for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":%%port "') do (
        if not "%%p"=="0" (
            taskkill /PID %%p /F >nul 2>&1
            if not errorlevel 1 (
                echo [OK]  Released port %%port (PID %%p)
            )
        )
    )
)

echo.
if %killed% EQU 0 (
    echo [INFO] No PAI processes were running.
) else (
    echo [OK]  PAI stopped ^(%killed% process group^(s^) terminated^).
)
echo.

endlocal
pause
