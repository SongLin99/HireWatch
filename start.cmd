@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 launcher.py %*
) else (
    python launcher.py %*
)
if errorlevel 1 (
    echo Setup failed. See docs/ or README.md. Python 3.12 is recommended.
    pause
)
