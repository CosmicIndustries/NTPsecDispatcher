@echo off
REM ============================================================
REM Time Sync Agent (Windows / W32Time)
REM - Memoization (%ProgramData%\TimeSync\config.cache)
REM - Telemetry with nanosecond timestamps
REM - Hardened pool fallbacks
REM - Privilege guardrails (PowerShell try/catch)
REM - Resilience (DNS warm-up, fallbacks)
REM - Clock step strategy (/resync, then /resync /force)
REM NOTE: W32Time does NOT support NTS; for NTS use Unix client.
REM Usage: time-sync-agent.bat [poolOverride] [--mode=fast|safe]
REM ============================================================

@echo off
:: Dynamic fallback for NTP pools

setlocal enabledelayedexpansion
set PRIMARY_POOLS=pool.chrony.eu pool.ntp.org time.cloudflare.com time.google.com
set SELECTED_POOL=

for %%P in (%PRIMARY_POOLS%) do (
    ping -n 1 %%P >nul 2>&1
    if !errorlevel! == 0 (
        set SELECTED_POOL=%%P
        goto :FOUND
    )
)

:FOUND
if "%SELECTED_POOL%"=="" (
    echo [WARN] No NTP pool reachable, will use dispatcher default
) else (
    echo [INFO] Selected reachable NTP pool: %SELECTED_POOL%
    set DISPATCHER_ARGS=--mode=fast --pool=%SELECTED_POOL%
)

:: Launch dispatcher
set PYTHON_PATH=C:\Python39\python.exe
set DISPATCHER_PATH=C:\Users\User\Desktop\time\dispatchService.py

%PYTHON_PATH% "%DISPATCHER_PATH%" %DISPATCHER_ARGS%


setlocal enabledelayedexpansion

REM --- Config ---
set "CACHE_DIR=%ProgramData%\TimeSync"
set "CACHE_FILE=%CACHE_DIR%\config.cache"
set "STATUS_LOG=%CACHE_DIR%\status.log"
set "CACHE_TTL_SECONDS=604800"
set "MIN_POLL_HEX=0x6"  REM 64s
set "MAX_POLL_HEX=0xa"  REM 1024s
set "PEER_FLAG=0x08"
set "DEFAULT_POOL=pool.chrony.eu"
set "FALLBACKS=time.cloudflare.com time.google.com pool.ntp.org"

REM --- Args ---
set "POOL_ARG="
set "MODE=safe"
for %%A in (%*) do (
  echo %%~A | findstr /i /b "--mode=" >nul && (
    for /f "tokens=2 delims==" %%m in ("%%~A") do set "MODE=%%m"
  )
)
for %%A in (%*) do (
  echo %%~A | findstr /i /b "--mode=" >nul || (
    if not defined POOL_ARG set "POOL_ARG=%%~A"
  )
)
if /i "%MODE%" NEQ "fast" if /i "%MODE%" NEQ "safe" set "MODE=safe"

REM --- Admin check ---
whoami /groups | findstr /i "S-1-5-32-544" >nul
if errorlevel 1 (
  echo [ERROR] Administrator privileges required.
  exit /b 1
)

REM --- Ensure cache dir ---
if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%" >nul 2>nul

REM --- Helper: ns timestamp via PowerShell ---
for /f "usebackq delims=" %%T in (`powershell -NoP -C "$t=[DateTimeOffset]::UtcNow; $t.ToString('yyyy-MM-ddTHH:mm:ss.fffffffZ')"`) do set "NOW_TS=%%T"

REM --- Logging helper ---
set "LOGPS=powershell -NoP -C"
set "ECHOLOG=%LOGPS% \"$p='%STATUS_LOG%'; New-Item -ItemType Directory -Force -Path (Split-Path $p) ^|^| Out-Null; Add-Content -Path $p -Value '[%NOW_TS%] ' + ($args -join ' ')\""

REM --- Load cache if fresh ---
set "USE_CACHE=0"
set "MY_TLD="
set "CACHED_AT="
if exist "%CACHE_FILE%" (
  for /f "tokens=1,2 delims==" %%A in (%CACHE_FILE%) do (
    if /i "%%~A"=="CACHED_AT" set "CACHED_AT=%%~B"
    if /i "%%~A"=="MY_TLD" set "MY_TLD=%%~B"
    if /i "%%~A"=="MODE" set "CACHED_MODE=%%~B"
  )
  for /f "usebackq delims=" %%E in (`powershell -NoP -C "(Get-Date -Date (Get-Date).ToUniversalTime() -UFormat %%s)"`) do set "NOW_EPOCH=%%E"
  if defined CACHED_AT (
    set /a "AGE=%NOW_EPOCH% - %CACHED_AT%"
    if %AGE% LSS %CACHE_TTL_SECONDS% set "USE_CACHE=1"
  )
)

REM --- Decide pool ---
if defined POOL_ARG (
  set "POOL_SEL=%POOL_ARG%"
) else if "%USE_CACHE%"=="1" (
  set "POOL_SEL=%MY_TLD%"
) else (
  set "POOL_SEL=%DEFAULT_POOL%"
)

REM --- DNS resolve helper (returns 0 if success) ---
set "RESOLVE_OK=0"
nslookup %POOL_SEL% >nul 2>nul && set "RESOLVE_OK=1"
if "%RESOLVE_OK%"=="0" (
  %ECHOLOG% "Primary pool '%POOL_SEL%' not resolvable, trying fallbacks..."
  for %%P in (%FALLBACKS%) do (
    nslookup %%P >nul 2>nul && (
      set "POOL_SEL=%%P"
      set "RESOLVE_OK=1"
      %ECHOLOG% "Using fallback pool '%%P'"
      goto :resolved
    )
  )
)
:resolved
if "%RESOLVE_OK%"=="0" (
  %ECHOLOG% "[FAIL] No resolvable pools."
  echo [FAIL] No resolvable pools.
  exit /b 2
)

REM --- Async DNS warm-up ---
start "" /b cmd /c "nslookup 1.%POOL_SEL% >nul 2>nul"
start "" /b cmd /c "nslookup 2.%POOL_SEL% >nul 2>nul"
start "" /b cmd /c "nslookup 3.%POOL_SEL% >nul 2>nul"
start "" /b cmd /c "nslookup 4.%POOL_SEL% >nul 2>nul"

REM --- Build server list ---
set "MY_SERVERS=1.%POOL_SEL%,%PEER_FLAG% 2.%POOL_SEL%,%PEER_FLAG% 3.%POOL_SEL%,%PEER_FLAG% 4.%POOL_SEL%,%PEER_FLAG%"

REM --- Memoize selection ---
for /f "usebackq delims=" %%E in (`powershell -NoP -C "(Get-Date -Date (Get-Date).ToUniversalTime() -UFormat %%s)"`) do set "NOW_EPOCH=%%E"
(
  echo CACHED_AT=%NOW_EPOCH%
  echo MY_TLD=%POOL_SEL%
  echo MODE=%MODE%
)> "%CACHE_FILE%"
%ECHOLOG% "Memoized pool=%POOL_SEL% mode=%MODE%"

REM --- Powershell guarded registry/service ops ---
set "PS=PowerShell -NoProfile -ExecutionPolicy Bypass -Command"

REM MinPollInterval
%PS% "try { reg add 'HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config' /v MinPollInterval /t REG_DWORD /d %MIN_POLL_HEX% /f ^| Out-Null; } catch { exit 1 }" || ( %ECHOLOG% "UAC/Registry error: MinPollInterval"; exit /b 1 )

REM MaxPollInterval
%PS% "try { reg add 'HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config' /v MaxPollInterval /t REG_DWORD /d %MAX_POLL_HEX% /f ^| Out-Null; } catch { exit 1 }" || ( %ECHOLOG% "UAC/Registry error: MaxPollInterval"; exit /b 1 )

REM Client Type NTP
%PS% "try { reg add 'HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' /v Type /t REG_SZ /d NTP /f ^| Out-Null; } catch { exit 1 }" || ( %ECHOLOG% "UAC/Registry error: Type"; exit /b 1 )

REM Register & restart service
%PS% "try { w32tm /register ^| Out-Null } catch {}"
%PS% "try { net stop w32time  ^| Out-Null } catch {}"
%PS% "try { net start w32time ^| Out-Null } catch { exit 1 }" || ( %ECHOLOG% "Service restart failed"; exit /b 1 )

REM Configure peers
%PS% "try { w32tm /config /manualpeerlist:'%MY_SERVERS%' /syncfromflags:manual /update ^| Out-Null } catch { exit 1 }" || ( %ECHOLOG% "w32tm config failed"; exit /b 1 )

REM Initial sync (gentle)
%PS% "try { w32tm /resync /nowait ^| Out-Null } catch {}"
REM Short settle
timeout /t 2 >nul

REM Telemetry: status dump
for /f "usebackq delims=" %%T in (`powershell -NoP -C "$t=[DateTimeOffset]::UtcNow; $t.ToString('yyyy-MM-ddTHH:mm:ss.fffffffZ')"`) do set "NOW_TS=%%T"
w32tm /query /status > "%TEMP%\w32stat.txt" 2>&1
for /f "usebackq delims=" %%L in ("%TEMP%\w32stat.txt") do %ECHOLOG% "%%L"

REM Clock step strategy:
REM - If still large skew, force resync
%PS% "$s=(w32tm /query /status) -join '`n'; if($s -match 'Phase Offset:\s*([-+0-9\.]+)s'){ $o=[double]$matches[1]; if([math]::Abs($o) -gt 1.0){ w32tm /resync /force | Out-Null; } }"

REM Create/update scheduled telemetry task (every 15 min)
schtasks /Query /TN "TimeSync_Telemetry" >nul 2>nul
if errorlevel 1 (
  %PS% "$p='%STATUS_LOG%'; $cmd='cmd /c for /f ""usebackq delims="" %%%%T in (`""" + 'powershell -NoP -C "$t=[DateTimeOffset]::UtcNow; $t.ToString(' + \"'yyyy-MM-ddTHH:mm:ss.fffffffZ'\" + ')"' + """` ) do @echo [%%%%T] && w32tm /query /status >> ""%STATUS_LOG%""'; schtasks /Create /SC MINUTE /MO 15 /TN TimeSync_Telemetry /TR $cmd /RU SYSTEM /RL HIGHEST /F | Out-Null"
)

echo [OK] Windows NTP client configured. Pool: %POOL_SEL% Mode: %MODE%
exit /b 0
