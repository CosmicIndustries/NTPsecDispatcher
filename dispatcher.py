#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-platform NTP Synchronization Dispatcher
---------------------------------------------
- Windows: auto-configure W32Time, auto-start service, scheduled telemetry
- Unix/macOS: configures chrony or ntpsec if available
- Nanosecond-level drift correction with memoization
- Logs verbose telemetry
- Fallback NTP pools
"""

import asyncio
import os
import platform
import subprocess
import sys
import json
from datetime import datetime

# -----------------------------
# Config
# -----------------------------
LOG_DIR = (
    r"C:\ProgramData\TimeSync"
    if platform.system() == "Windows"
    else "/var/log/time-sync"
)
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "status.log")
MEMO_FILE = os.path.join(LOG_DIR, "memo.json")

POOLS = [
    "pool.chrony.eu",
    "pool.ntp.org",
    "time.cloudflare.com",
    "time.google.com",
]

# -----------------------------
# Helper Functions
# -----------------------------
def timestamp():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def log(msg: str):
    """Verbose logging to console + file"""
    line = f"[{timestamp()}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def run_cmd(cmd: str) -> str:
    """Run a shell command asynchronously, capture stdout + stderr"""
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    result = out.decode(errors="ignore").strip()
    err_str = err.decode(errors="ignore").strip()
    if err_str:
        log(f"[CMD-ERR] {cmd} => {err_str}")
    return result

# -----------------------------
# Windows Functions
# -----------------------------
async def windows_config():
    log("Configuring Windows Time Service...")

    # Registry edits
    reg_cmds = [
        r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config" /v MinPollInterval /t REG_DWORD /d 0x6 /f',
        r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config" /v MaxPollInterval /t REG_DWORD /d 0xa /f',
        r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Parameters" /v Type /t REG_SZ /d NTP /f',
    ]
    for cmd in reg_cmds:
        await run_cmd(cmd)

    # Ensure w32time service enabled & running
    status = await run_cmd("sc query w32time")
    if "STOPPED" in status or "DISABLED" in status.upper():
        log("[WARN] Windows Time service not running or disabled, enabling...")
        await run_cmd("sc config w32time start= auto")
        await run_cmd("sc start w32time")

    # Restart service to apply registry changes
    await run_cmd("net stop w32time")
    await run_cmd("net start w32time")

    # Configure NTP pools
    success = False
    for pool in POOLS:
        servers = " ".join([f"{i}.{pool},0x8" for i in range(4)])
        cmd = f'w32tm /config /manualpeerlist:"{servers}" /syncfromflags:manual /update'
        result = await run_cmd(cmd)
        if result:
            log(f"[OK] Configured {pool}")
            await run_cmd("w32tm /resync /force")
            success = True
            break
        else:
            log(f"[FAIL] Could not configure {pool}")

    if not success:
        log("[FAIL] No NTP pools could be configured.")

    # Telemetry logging
    await log_windows_status()

    # Scheduled task for auto-run every 30 minutes
    await create_windows_task()

# -----------------------------
# Windows Telemetry & Drift Correction
# -----------------------------
async def log_windows_status():
    """Log current NTP status and peers"""
    log("[INFO] Logging telemetry...")
    status_out = await run_cmd("w32tm /query /status")
    peers_out = await run_cmd("w32tm /query /peers")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n===== Sync Report {timestamp()} =====\n")
        f.write(status_out + "\n")
        f.write(peers_out + "\n")
    log("[INFO] Telemetry written to " + LOG_FILE)

async def drift_correction_windows():
    """High-precision drift correction for Windows"""
    log("[INFO] Checking clock skew for high-precision adjustment...")
    output = await run_cmd("w32tm /stripchart /computer:time.windows.com /dataonly /samples:1")
    try:
        offset_line = [l for l in output.splitlines() if "," in l][-1]
        skew_s = float(offset_line.split(",")[1].strip().replace("s", ""))
        skew_ns = skew_s * 1e9
    except Exception:
        skew_ns = None
        log("[WARN] Could not parse skew, defaulting to forced resync")

    # Load last memoized skew
    last_skew_ns = 0
    if os.path.exists(MEMO_FILE):
        with open(MEMO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            last_skew_ns = data.get("last_skew_ns", 0)

    if skew_ns is not None:
        log(f"[INFO] Current skew: {skew_ns:.0f} ns (last: {last_skew_ns:.0f} ns)")
        if 1e3 < abs(skew_ns) < 1e6:  # small skew: 1 ns < skew < 1 ms
            await run_cmd("w32tm /resync /nowait")
            log("[INFO] Applied precise incremental resync for small skew")
        elif 1e8 < abs(skew_ns) < 1e9:  # large skew: 100 ms < skew < 1 s
            await run_cmd("w32tm /resync /force")
            log("[INFO] Applied forced resync for large skew")
        else:
            log("[INFO] Skew negligible; no adjustment needed")

        # Memoize current skew
        with open(MEMO_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_skew_ns": skew_ns}, f)

async def create_windows_task():
    """Create scheduled task for auto telemetry every 30 minutes"""
    task_name = "TimeSyncTelemetry"
    python_path = sys.executable
    script_path = os.path.abspath(__file__)
    cmd = (
        f'schtasks /Create /F /SC MINUTE /MO 30 /TN {task_name} '
        f'/TR "\\"{python_path}\\" \\"{script_path}\\" --mode=fast"'
    )
    await run_cmd(cmd)
    log(f"[TASK] Scheduled telemetry every 30 minutes under task '{task_name}'")

# -----------------------------
# Unix Functions
# -----------------------------
async def unix_config():
    log("Configuring Unix NTP client...")
    tool = None
    for bin in ["chronyc", "ntpsec-ntpdate"]:
        result = await run_cmd(f"which {bin}")
        if result:
            tool = bin
            break
    if not tool:
        log("[ERR] No chrony or ntpsec found")
        return

    success = False
    for pool in POOLS:
        cmd = f"{tool} -a makestep; {tool} add server {pool} iburst"
        result = await run_cmd(cmd)
        if result:
            log(f"[OK] Configured {pool}")
            success = True
            break
        else:
            log(f"[FAIL] Pool {pool}: could not configure")
    if not success:
        log("[FAIL] No NTP pools could be configured")

    # Telemetry logging
    status = await run_cmd(f"{tool} tracking")
    sources = await run_cmd(f"{tool} sources -v")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n===== Sync Report {timestamp()} =====\n")
        f.write(status + "\n")
        f.write(sources + "\n")
    log("[INFO] Telemetry written to " + LOG_FILE)

async def drift_correction_unix():
    """High-precision drift correction for Unix"""
    log("[INFO] Checking clock skew for high-precision adjustment...")
    output = await run_cmd("chronyc tracking")
    try:
        for line in output.splitlines():
            if "Last offset" in line:
                skew_s = float(line.split(":")[1].split()[0])
                skew_ns = skew_s * 1e9
                break
        else:
            skew_ns = None
    except Exception:
        skew_ns = None

    if skew_ns is not None:
        log(f"[INFO] Current skew: {skew_ns:.0f} ns")
        if 1e3 < abs(skew_ns) < 1e6:
            await run_cmd("chronyc makestep 0.001 3")
            log("[INFO] Applied precise incremental adjustment")
        elif 1e8 < abs(skew_ns) < 1e9:
            await run_cmd("chronyc makestep")
            log("[INFO] Applied forced step for large skew")
        else:
            log("[INFO] Skew negligible; no adjustment needed")

# -----------------------------
# Main
# -----------------------------
async def main():
    log(f"Dispatcher start on {platform.system()}")
    if platform.system() == "Windows":
        await windows_config()
        await drift_correction_windows()
    else:
        await unix_config()
        await drift_correction_unix()
    log("Dispatcher complete.")

if __name__ == "__main__":
    asyncio.run(main())
