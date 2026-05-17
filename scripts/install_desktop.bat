@echo off
:: ==============================================================================
:: install_desktop.bat — Create AIO Desktop shortcuts on Windows
:: Uses PowerShell to create proper .lnk shortcut files.
::
:: Usage:
::   install_desktop.bat          create shortcuts
::   install_desktop.bat /remove  remove shortcuts
:: ==============================================================================

setlocal enabledelayedexpansion

:: AIO root is one level above this scripts\ folder
for %%d in ("%~dp0..") do set "AIO_DIR=%%~fd"
set "DESKTOP=%USERPROFILE%\Desktop"
set "START_MENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs\AIO Local AI"

if /i "%~1"=="/remove" goto :remove_shortcuts
if /i "%~1"=="--remove" goto :remove_shortcuts

:: ── Create Start Menu folder ──────────────────────────────────────────────────
if not exist "%START_MENU%" mkdir "%START_MENU%"

echo [AIO] Creating Desktop and Start Menu shortcuts...

:: ── PowerShell snippet to create .lnk files ──────────────────────────────────
:: Launch AIO
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$s = $ws.CreateShortcut('%DESKTOP%\Launch AIO.lnk'); " ^
    "$s.TargetPath = 'cmd.exe'; " ^
    "$s.Arguments = '/k cd /d \"%AIO_DIR%\" && runaio.bat'; " ^
    "$s.WorkingDirectory = '%AIO_DIR%'; " ^
    "$s.Description = 'Launch AIO All-In-One Local AI'; " ^
    "$s.IconLocation = 'shell32.dll,77'; " ^
    "$s.Save()"

:: Stop AIO
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$s = $ws.CreateShortcut('%DESKTOP%\Stop AIO.lnk'); " ^
    "$s.TargetPath = 'cmd.exe'; " ^
    "$s.Arguments = '/k cd /d \"%AIO_DIR%\" && stopaio.bat'; " ^
    "$s.WorkingDirectory = '%AIO_DIR%'; " ^
    "$s.Description = 'Stop AIO All-In-One Local AI'; " ^
    "$s.IconLocation = 'shell32.dll,131'; " ^
    "$s.Save()"

:: Start Menu copies
powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$s = $ws.CreateShortcut('%START_MENU%\Launch AIO.lnk'); " ^
    "$s.TargetPath = 'cmd.exe'; " ^
    "$s.Arguments = '/k cd /d \"%AIO_DIR%\" && runaio.bat'; " ^
    "$s.WorkingDirectory = '%AIO_DIR%'; " ^
    "$s.Description = 'Launch AIO All-In-One Local AI'; " ^
    "$s.IconLocation = 'shell32.dll,77'; " ^
    "$s.Save()"

powershell -NoProfile -Command ^
    "$ws = New-Object -ComObject WScript.Shell; " ^
    "$s = $ws.CreateShortcut('%START_MENU%\Stop AIO.lnk'); " ^
    "$s.TargetPath = 'cmd.exe'; " ^
    "$s.Arguments = '/k cd /d \"%AIO_DIR%\" && stopaio.bat'; " ^
    "$s.WorkingDirectory = '%AIO_DIR%'; " ^
    "$s.Description = 'Stop AIO All-In-One Local AI'; " ^
    "$s.IconLocation = 'shell32.dll,131'; " ^
    "$s.Save()"

echo [OK] Desktop shortcuts created:
echo      %DESKTOP%\Launch AIO.lnk
echo      %DESKTOP%\Stop AIO.lnk
echo [OK] Start Menu entries created:
echo      %START_MENU%\

goto :end

:remove_shortcuts
del /f /q "%DESKTOP%\Launch AIO.lnk" 2>nul
del /f /q "%DESKTOP%\Stop AIO.lnk"   2>nul
if exist "%START_MENU%" rmdir /s /q "%START_MENU%"
echo [OK] Shortcuts removed.
goto :end

:end
endlocal
pause
