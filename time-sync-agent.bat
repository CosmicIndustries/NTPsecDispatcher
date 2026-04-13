@echo off
:: ============================================================
:: time-sync-agent.bat
:: NTPsecDispatcher Windows launcher
::
:: Pings candidate pools, picks the first reachable one,
:: then launches dispatcher.py with --mode and --pool args.
::
:: Usage:
::   time-sync-agent.bat [--mode=fast|ultrafast|lazy] [POOL_OVERRIDE]
::
:: Requirements:
::   - Python 3.9+ on PATH  (or set PYTHON_PATH below)
::   - dispatcher.py in the same folder as this script
::   - Administrator privileges for W32Time configuration
::
:: NOTE: W32Time does NOT support NTS. Use the Unix client for NTS.
:: ============================================================

setlocal enabledelayedexpansion

:: ---- Admin check ----
whoami /groups | findstr /i "S-1-5-32-544" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] This script requires Administrator privileges.
    echo         Right-click and choose "Run as Administrator".
    exit /b 1
)

:: ---- Paths ----
:: If python is on PATH this resolves automatically.
:: Override PYTHON_PATH below only if needed.
set "PYTHON_PATH=python"
set "DISPATCHER_PATH=%~dp0dispatcher.py"

if not exist "%DISPATCHER_PATH%" (
    echo [ERROR] dispatcher.py not found at %DISPATCHER_PATH%
    exit /b 1
)

:: ---- Mode (default: fast) ----
set "MODE=fast"
for %%A in (%*) do (
    echo %%~A | findstr /i /b "--mode=" >nul && (
        for /f "tokens=2 delims==" %%m in ("%%~A") do set "MODE=%%m"
    )
)

:: ---- Pool override ----
set "POOL_OVERRIDE="
for %%A in (%*) do (
    echo %%~A | findstr /i /b "--mode=" >nul || (
        echo %%~A | findstr /i /b "--" >nul || (
            if not defined POOL_OVERRIDE set "POOL_OVERRIDE=%%~A"
        )
    )
)

:: ---- Candidate pools ----
set POOLS=pool.chrony.eu pool.ntp.org time.cloudflare.com time.google.com 0.europe.pool.ntpsec.org 1.north-america.pool.ntpsec.org

:: ---- If pool was manually specified, use it directly ----
if defined POOL_OVERRIDE (
    echo [INFO] Using manually specified pool: %POOL_OVERRIDE%
    set "SELECTED_POOL=%POOL_OVERRIDE%"
    goto :LAUNCH
)

:: ---- Ping-probe to find first reachable pool ----
set "SELECTED_POOL="
for %%P in (%POOLS%) do (
    if not defined SELECTED_POOL (
        ping -n 1 -w 1000 %%P >nul 2>&1
        if !errorlevel! == 0 (
            set "SELECTED_POOL=%%P"
            echo [INFO] Selected reachable pool: %%P
        )
    )
)

if not defined SELECTED_POOL (
    echo [WARN] No pool reachable via ping — launching dispatcher with defaults.
)

:LAUNCH
:: ---- Build arg string ----
set "DISPATCHER_ARGS=--mode=%MODE%"
if defined SELECTED_POOL set "DISPATCHER_ARGS=%DISPATCHER_ARGS% --pool=%SELECTED_POOL%"

echo [INFO] Launching: %PYTHON_PATH% "%DISPATCHER_PATH%" %DISPATCHER_ARGS%
"%PYTHON_PATH%" "%DISPATCHER_PATH%" %DISPATCHER_ARGS%

if errorlevel 1 (
    echo [ERROR] Dispatcher exited with error code %errorlevel%
    exit /b %errorlevel%
)

endlocal
exit /b 0
