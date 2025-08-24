#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cross-platform NTP Synchronization Dispatcher with SYSTEM service support
Supports: fast, ultrafast, lazy modes
"""

import asyncio
import os
import platform
import sys
import json
from datetime import datetime
import argparse

# -----------------------------
# CLI Arguments
# -----------------------------
parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=["fast", "ultrafast", "lazy"], default="fast")
args = parser.parse_args()
MODE = args.mode

# -----------------------------
# Config based on mode
# -----------------------------
if platform.system() == "Windows":
    LOG_DIR = r"C:\ProgramData\TimeSync"
else:
    LOG_DIR = "/var/log/time-sync"
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "status.log")
MEMO_FILE = os.path.join(LOG_DIR, "memo.json")

# Mode parameters
if MODE == "ultrafast":
    CHECK_INTERVAL = 5  # seconds
    PRECISION_THRESHOLD_NS = 1e3  # adjust even for tiny skew
elif MODE == "lazy":
    CHECK_INTERVAL = 1800  # 30 min
    PRECISION_THRESHOLD_NS = 1e6  # skip tiny skews
else:  # fast
    CHECK_INTERVAL = 60  # 1 min
    PRECISION_THRESHOLD_NS = 1e5

# Pools
POOLS = [
    "pool.chrony.eu",
    "pool.ntp.org",
    "time.cloudflare.com",
    "time.google.com",
    "0.europe.pool.ntpsec.org",
    "1.north-america.pool.ntpsec.org",
    "2.asia.pool.ntpsec.org",
]

# -----------------------------
# Helpers
# -----------------------------
def timestamp():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def log(msg: str):
    line = f"[{timestamp()}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


async def run_cmd(cmd: str) -> str:
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

    reg_cmds = [
        r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config" /v MinPollInterval /t REG_DWORD /d 0x6 /f',
        r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config" /v MaxPollInterval /t REG_DWORD /d 0xa /f',
        r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Parameters" /v Type /t REG_SZ /d NTP /f',
    ]
    for cmd in reg_cmds:
        await run_cmd(cmd)

    status = await run_cmd("sc query w32time")
    if "STOPPED" in status or "DISABLED" in status.upper():
        log("[WARN] Windows Time service not running or disabled, enabling...")
        await run_cmd("sc config w32time start= auto")
        await run_cmd("sc start w32time")

    await run_cmd("net stop w32time")
    await run_cmd("net start w32time")

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

    await log_windows_status()
    await drift_correction_windows()
    await create_windows_service()

async def log_windows_status():
    log("[INFO] Logging telemetry...")
    status_out = await run_cmd("w32tm /query /status")
    peers_out = await run_cmd("w32tm /query /peers")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n===== Sync Report {timestamp()} =====\n")
        f.write(status_out + "\n")
        f.write(peers_out + "\n")
    log("[INFO] Telemetry written to " + LOG_FILE)

async def drift_correction_windows():
    log("[INFO] Checking clock skew for high-precision adjustment...")
    output = await run_cmd("w32tm /stripchart /computer:time.windows.com /dataonly /samples:1")
    try:
        offset_line = [l for l in output.splitlines() if "," in l][-1]
        skew_s = float(offset_line.split(",")[1].strip().replace("s", ""))
        skew_ns = skew_s * 1e9
    except Exception:
        skew_ns = None
        log("[WARN] Could not parse skew, defaulting to forced resync")

    last_skew_ns = 0
    if os.path.exists(MEMO_FILE):
        with open(MEMO_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            last_skew_ns = data.get("last_skew_ns", 0)

    if skew_ns is not None:
        log(f"[INFO] Current skew: {skew_ns:.0f} ns (last: {last_skew_ns:.0f} ns)")
        if PRECISION_THRESHOLD_NS < abs(skew_ns) < 1e6:
            await run_cmd("w32tm /resync /nowait")
            log("[INFO] Applied precise incremental resync for small skew")
        elif 1e8 < abs(skew_ns) < 1e9:
            await run_cmd("w32tm /resync /force")
            log("[INFO] Applied forced resync for large skew")
        else:
            log("[INFO] Skew negligible; no adjustment needed")

        with open(MEMO_FILE, "w", encoding="utf-8") as f:
            json.dump({"last_skew_ns": skew_ns}, f)

async def create_windows_service():
    """Wrap dispatcher as SYSTEM service via NSSM if available"""
    nssm_path = r"C:\nssm\nssm.exe"
    if os.path.exists(nssm_path):
        cmd_install = f'"{nssm_path}" install TimeSyncAgent "{sys.executable}" "{os.path.abspath(__file__)} --mode={MODE}"'
        await run_cmd(cmd_install)
        await run_cmd(f'"{nssm_path}" set TimeSyncAgent Start SERVICE_AUTO_START')
        await run_cmd(f'"{nssm_path}" start TimeSyncAgent')
        log("[TASK] Dispatcher installed as SYSTEM service via NSSM")
    else:
        log("[WARN] NSSM not found, fallback to scheduled task or manual service install")

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

    status = await run_cmd(f"{tool} tracking")
    sources = await run_cmd(f"{tool} sources -v")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n===== Sync Report {timestamp()} =====\n")
        f.write(status + "\n")
        f.write(sources + "\n")
    log("[INFO] Telemetry written to " + LOG_FILE)
    await drift_correction_unix(tool)

async def drift_correction_unix(tool: str):
    log("[INFO] Checking clock skew for high-precision adjustment...")
    output = await run_cmd(f"{tool} tracking")
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
        if PRECISION_THRESHOLD_NS < abs(skew_ns) < 1e6:
            await run_cmd(f"{tool} makestep 0.001 3")
            log("[INFO] Applied precise incremental adjustment")
        elif 1e8 < abs(skew_ns) < 1e9:
            await run_cmd(f"{tool} makestep")
            log("[INFO] Applied forced step for large skew")
        else:
            log("[INFO] Skew negligible; no adjustment needed")

# -----------------------------
# Main
# -----------------------------
async def main():
    log(f"Dispatcher start on {platform.system()} | Mode={MODE}")
    if platform.system() == "Windows":
        await windows_config()
    else:
        await unix_config()
    log("Dispatcher complete.")

if __name__ == "__main__":
    asyncio.run(main())
