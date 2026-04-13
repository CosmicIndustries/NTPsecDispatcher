#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NTPsecDispatcher — unified cross-platform NTP synchronization agent.

Modes:
  fast      — one-shot config + single drift check (default)
  ultrafast — continuous nanosecond-level polling loop (daemon use)
  lazy      — one-shot, low-sensitivity thresholds

Platforms: Windows (W32Time + NSSM), Linux/macOS (chrony → ntpsec → timesyncd)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import signal
import sys
from datetime import datetime, timezone
from typing import Optional

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Cross-platform NTP synchronization dispatcher",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--mode",
        choices=["fast", "ultrafast", "lazy"],
        default="fast",
        help="Sync frequency/sensitivity mode",
    )
    p.add_argument(
        "--pool",
        default=None,
        metavar="HOST",
        help="Override NTP pool (e.g. pool.chrony.eu)",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

IS_WINDOWS = platform.system() == "Windows"

LOG_DIR = r"C:\ProgramData\TimeSync" if IS_WINDOWS else "/var/log/time-sync"
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE  = os.path.join(LOG_DIR, "status.log")
MEMO_FILE = os.path.join(LOG_DIR, "memo.json")

# Pool priority list — ordered by preference.
# Forced pool (--pool) is prepended at runtime.
DEFAULT_POOLS: list[str] = [
    "pool.chrony.eu",
    "pool.ntp.org",
    "time.cloudflare.com",
    "time.google.com",
    "0.europe.pool.ntpsec.org",
    "1.north-america.pool.ntpsec.org",
    "2.asia.pool.ntpsec.org",
]

# Per-mode tuning
MODE_CONFIG: dict[str, dict] = {
    "ultrafast": {"interval": 5,    "threshold_ns": 1_000},        # 1 µs
    "fast":      {"interval": 60,   "threshold_ns": 100_000},      # 0.1 ms
    "lazy":      {"interval": 1800, "threshold_ns": 1_000_000},    # 1 ms
}

# Skew decision bands (nanoseconds)
SMALL_SKEW_MAX_NS  = 1_000_000       # 1 ms  — incremental resync
LARGE_SKEW_MIN_NS  = 100_000_000     # 100 ms — forced step
LARGE_SKEW_MAX_NS  = 1_000_000_000   # 1 s   — cap (>1 s = probably clock jump, not drift)

CMD_TIMEOUT = 15  # seconds before a subprocess is considered hung

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def log(msg: str) -> None:
    line = f"[{_utc_ts()}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:
        print(f"[LOG-ERR] Cannot write log: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Shell runner
# ---------------------------------------------------------------------------

async def run_cmd(cmd: str) -> str:
    """
    Run *cmd* in a shell, return stdout as str.
    Stderr is logged at [CMD-ERR] level.
    Returns "" on timeout or error (never raises).
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=CMD_TIMEOUT)
        stdout = out.decode(errors="ignore").strip()
        stderr = err.decode(errors="ignore").strip()
        if stderr:
            log(f"[CMD-ERR] {cmd!r} => {stderr}")
        return stdout
    except asyncio.TimeoutError:
        log(f"[CMD-ERR] Timeout ({CMD_TIMEOUT}s): {cmd!r}")
        return ""
    except Exception as exc:
        log(f"[CMD-ERR] Exception running {cmd!r}: {exc}")
        return ""


# ---------------------------------------------------------------------------
# Memoization
# ---------------------------------------------------------------------------

def load_memo() -> dict:
    try:
        if os.path.exists(MEMO_FILE):
            with open(MEMO_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as exc:
        log(f"[WARN] Could not load memo: {exc}")
    return {}


def save_memo(data: dict) -> None:
    """Atomic write via temp-file rename to prevent corruption on crash."""
    tmp = MEMO_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, MEMO_FILE)
    except Exception as exc:
        log(f"[WARN] Could not save memo: {exc}")


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

async def windows_configure(pools: list[str]) -> None:
    """Apply registry tuning, ensure W32Time is running, configure peers."""
    log("Configuring Windows Time Service...")

    # Registry: poll intervals + client type (no PowerShell dependency)
    for cmd in (
        r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config"'
        r' /v MinPollInterval /t REG_DWORD /d 0x6 /f',

        r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Config"'
        r' /v MaxPollInterval /t REG_DWORD /d 0xa /f',

        r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\Parameters"'
        r' /v Type /t REG_SZ /d NTP /f',

        r'reg add "HKLM\SYSTEM\CurrentControlSet\Services\W32Time\TimeProviders\NtpClient"'
        r' /v Enabled /t REG_DWORD /d 0x1 /f',
    ):
        await run_cmd(cmd)

    # Enable NtpClient provider if disabled
    await run_cmd("w32tm /register")

    # Heal service if not running
    status = await run_cmd("sc query w32time")
    if "STOPPED" in status or "DISABLED" in status.upper():
        log("[WARN] W32Time not running — enabling...")
        await run_cmd("sc config w32time start= auto")
        await run_cmd("sc start w32time")

    # Restart to pick up registry changes
    await run_cmd("net stop w32time")
    await run_cmd("net start w32time")

    # Try pools in priority order
    for pool in pools:
        peer_list = " ".join(f"{i}.{pool},0x8" for i in range(1, 5))
        result = await run_cmd(
            f'w32tm /config /manualpeerlist:"{peer_list}" /syncfromflags:manual /update'
        )
        if result:
            log(f"[OK] Peers configured: {pool}")
            await run_cmd("w32tm /resync /nowait")
            break
        log(f"[FAIL] Could not configure pool: {pool}")
    else:
        log("[FAIL] All pools exhausted — W32Time left with prior config")

    await _windows_telemetry()


async def _windows_telemetry() -> None:
    status  = await run_cmd("w32tm /query /status")
    peers   = await run_cmd("w32tm /query /peers")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== Sync Report {_utc_ts()} =====\n")
            fh.write(status + "\n")
            fh.write(peers  + "\n")
    except OSError as exc:
        log(f"[WARN] Telemetry write failed: {exc}")
    log(f"[INFO] Telemetry written → {LOG_FILE}")


async def windows_measure_skew() -> Optional[int]:
    """
    Measure clock offset via w32tm stripchart.
    Returns nanoseconds (int) or None on parse failure.
    Fallback: parse 'w32tm /query /status' offset line.
    """
    # Primary: stripchart gives a clean "HH:MM:SS, -0.0090668s" line
    out = await run_cmd(
        "w32tm /stripchart /computer:time.windows.com /dataonly /samples:1"
    )
    comma_lines = [l.strip() for l in out.splitlines() if "," in l]
    if comma_lines:
        try:
            raw = comma_lines[-1].split(",", 1)[1].strip().rstrip("s")
            return int(float(raw) * 1e9)
        except (ValueError, IndexError):
            pass

    # Fallback: /query /status offset line
    import re
    out2 = await run_cmd("w32tm /query /status")
    for line in out2.splitlines():
        m = re.search(r"([-+]?\d+\.\d+)\s*s", line)
        if m and ("Offset" in line or "offset" in line):
            try:
                return int(float(m.group(1)) * 1e9)
            except ValueError:
                continue
    return None


async def windows_drift_correct(threshold_ns: int) -> None:
    memo = load_memo()
    last_ns = memo.get("last_skew_ns", 0)

    skew_ns = await windows_measure_skew()
    if skew_ns is None:
        log("[WARN] Skew measurement failed — issuing precautionary resync")
        await run_cmd("w32tm /resync /force")
        return

    log(f"[INFO] Skew: {skew_ns:+,} ns (prev: {last_ns:+,} ns)")
    memo["last_skew_ns"] = skew_ns
    save_memo(memo)

    abs_skew = abs(skew_ns)
    if abs_skew < threshold_ns:
        log("[INFO] Skew within tolerance — no action")
    elif abs_skew < SMALL_SKEW_MAX_NS:
        await run_cmd("w32tm /resync /nowait")
        log("[ACTION] Incremental resync (small skew)")
    elif LARGE_SKEW_MIN_NS < abs_skew < LARGE_SKEW_MAX_NS:
        await run_cmd("w32tm /resync /force")
        log("[ACTION] Forced step (large skew)")
    else:
        log(f"[INFO] Skew {abs_skew:,} ns outside actionable band — no action")


async def windows_install_service(mode: str) -> None:
    """Install this script as a SYSTEM service via NSSM (optional)."""
    nssm = r"C:\nssm\nssm.exe"
    if not os.path.exists(nssm):
        log("[WARN] NSSM not found — skipping service install (manual or schtasks fallback)")
        # Fallback: scheduled task every 15 min
        task_cmd = (
            f'schtasks /Create /F /SC MINUTE /MO 15 /TN TimeSyncAgent /RU SYSTEM /RL HIGHEST'
            f' /TR "\\"{sys.executable}\\" \\"{os.path.abspath(__file__)}\\" --mode={mode}"'
        )
        await run_cmd(task_cmd)
        log("[TASK] Scheduled task 'TimeSyncAgent' created (15-min interval)")
        return

    script = os.path.abspath(__file__)
    await run_cmd(f'"{nssm}" install TimeSyncAgent "{sys.executable}" "{script} --mode={mode}"')
    await run_cmd(f'"{nssm}" set TimeSyncAgent Start SERVICE_AUTO_START')
    await run_cmd(f'"{nssm}" start TimeSyncAgent')
    log("[SERVICE] Installed as SYSTEM service via NSSM")


# ---------------------------------------------------------------------------
# Unix / macOS
# ---------------------------------------------------------------------------

async def _detect_unix_tool() -> Optional[str]:
    """Return first available time sync tool, or None."""
    for candidate in ("chronyc", "ntpq", "timedatectl"):
        if await run_cmd(f"command -v {candidate}"):
            return candidate
    return None


async def unix_configure(pools: list[str], mode: str) -> None:
    """
    Write a proper config file for chrony/ntpsec/timesyncd then restart
    the relevant daemon.  Unlike the original, this persists across reboots.
    """
    log("Configuring Unix NTP client...")

    minpoll, maxpoll, makestep = (4, 8, "0.1 10") if mode == "fast" else (6, 10, "1.0 3")
    primary = pools[0]

    # --- chrony (preferred) ---
    if await run_cmd("command -v chronyd"):
        conf_candidates = ("/etc/chrony/chrony.conf", "/etc/chrony.conf")
        conf = next((p for p in conf_candidates if os.path.exists(p)), conf_candidates[0])
        _backup(conf)

        nts_supported = bool(await run_cmd("chronyc --help 2>&1 | grep -i nts"))
        nts_line = " nts" if nts_supported else ""

        lines = [
            "# Managed by NTPsecDispatcher — do not edit by hand",
            "driftfile /var/lib/chrony/chrony.drift",
            f"makestep {makestep}",
            "rtcsync",
            "leapsectz right/UTC",
            "logdir /var/log/chrony",
        ]
        for i in range(1, 5):
            lines.append(f"pool {i}.{primary} iburst minpoll {minpoll} maxpoll {maxpoll}")
        # NTS-capable fallback servers (if chrony supports it)
        lines.append(
            f"server time.cloudflare.com iburst{nts_line} minpoll {minpoll} maxpoll {maxpoll}"
        )
        lines.append(
            f"server time.google.com iburst{nts_line} minpoll {minpoll} maxpoll {maxpoll}"
        )

        _write_config(conf, "\n".join(lines))
        await run_cmd("systemctl restart chronyd 2>/dev/null || service chrony restart 2>/dev/null || true")
        await _chrony_telemetry()
        await _install_systemd_timer(
            "chronyc tracking; chronyc sources -v", LOG_FILE
        )
        log(f"[OK] chrony configured — pool={primary} mode={mode}")
        return

    # --- ntpsec / ntpd ---
    if await run_cmd("command -v ntpd"):
        conf_candidates = ("/etc/ntpsec/ntp.conf", "/etc/ntp.conf")
        conf = next((p for p in conf_candidates if os.path.exists(p)), conf_candidates[1])
        _backup(conf)

        lines = [
            "# Managed by NTPsecDispatcher — do not edit by hand",
            "driftfile /var/lib/ntp/drift",
            "tinker panic 0",
        ]
        for i in range(1, 5):
            lines.append(f"pool {i}.{primary} iburst minpoll {minpoll} maxpoll {maxpoll}")

        _write_config(conf, "\n".join(lines))
        await run_cmd(
            "systemctl restart ntpsec 2>/dev/null || systemctl restart ntp 2>/dev/null "
            "|| service ntpsec restart 2>/dev/null || service ntp restart 2>/dev/null || true"
        )
        await _install_systemd_timer("ntpq -p", LOG_FILE)
        log(f"[OK] ntpsec/ntp configured — pool={primary} mode={mode}")
        return

    # --- systemd-timesyncd (last resort) ---
    if await run_cmd("systemctl list-unit-files 2>/dev/null | grep -q systemd-timesyncd && echo yes"):
        conf = "/etc/systemd/timesyncd.conf"
        _backup(conf)
        ntp_list = " ".join(
            f"{i}.{primary}" for i in range(1, 5)
        ) + " time.cloudflare.com time.google.com"
        _write_config(conf, f"# Managed by NTPsecDispatcher\n[Time]\nNTP={ntp_list}\n")
        await run_cmd("systemctl restart systemd-timesyncd || true")
        log(f"[OK] systemd-timesyncd configured — pool={primary}")
        return

    log("[ERR] No supported NTP daemon found (chrony/ntpsec/timesyncd). Install chrony.")


def _backup(path: str) -> None:
    if os.path.exists(path):
        import shutil
        dst = f"{path}.bak.{int(datetime.now(timezone.utc).timestamp())}"
        try:
            shutil.copy2(path, dst)
        except OSError:
            pass


def _write_config(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(content + "\n")
    os.replace(tmp, path)


async def _chrony_telemetry() -> None:
    tracking = await run_cmd("chronyc tracking")
    sources  = await run_cmd("chronyc sources -v")
    try:
        with open(LOG_FILE, "a") as fh:
            fh.write(f"\n===== Sync Report {_utc_ts()} =====\n")
            fh.write(tracking + "\n")
            fh.write(sources  + "\n")
    except OSError:
        pass


async def _install_systemd_timer(exec_cmd: str, log_path: str) -> None:
    if not await run_cmd("command -v systemctl"):
        return
    svc = "/etc/systemd/system/time-sync-telemetry.service"
    tmr = "/etc/systemd/system/time-sync-telemetry.timer"

    _write_config(svc, f"""\
[Unit]
Description=Time Sync Telemetry

[Service]
Type=oneshot
ExecStart=/bin/bash -c '(date; {exec_cmd}) >> {log_path}'
""")
    _write_config(tmr, """\
[Unit]
Description=Time Sync Telemetry timer

[Timer]
OnBootSec=2m
OnUnitActiveSec=15m
Unit=time-sync-telemetry.service

[Install]
WantedBy=timers.target
""")
    await run_cmd("systemctl daemon-reload || true")
    await run_cmd("systemctl enable --now time-sync-telemetry.timer || true")


async def unix_measure_skew() -> Optional[int]:
    """Try chronyc tracking, then ntpq -c rv. Returns nanoseconds or None."""
    # chrony
    out = await run_cmd("chronyc tracking")
    for line in out.splitlines():
        if "Last offset" in line:
            try:
                val = line.split(":", 1)[1].strip().split()[0]
                return int(float(val) * 1e9)
            except (ValueError, IndexError):
                break

    # ntpq fallback
    out2 = await run_cmd("ntpq -c rv")
    for token in out2.replace(",", " ").split():
        if token.startswith("offset="):
            try:
                return int(float(token.split("=", 1)[1]) * 1e9)
            except ValueError:
                pass
    return None


async def unix_drift_correct(threshold_ns: int) -> None:
    memo = load_memo()
    last_ns = memo.get("last_skew_ns", 0)

    skew_ns = await unix_measure_skew()
    if skew_ns is None:
        log("[WARN] Could not measure skew on Unix")
        return

    log(f"[INFO] Skew: {skew_ns:+,} ns (prev: {last_ns:+,} ns)")
    memo["last_skew_ns"] = skew_ns
    save_memo(memo)

    abs_skew = abs(skew_ns)
    if abs_skew < threshold_ns:
        log("[INFO] Skew within tolerance — no action")
    elif abs_skew < SMALL_SKEW_MAX_NS:
        await run_cmd("chronyc makestep 0.001 3 2>/dev/null || true")
        log("[ACTION] Incremental chrony step (small skew)")
    elif LARGE_SKEW_MIN_NS < abs_skew < LARGE_SKEW_MAX_NS:
        await run_cmd("chronyc makestep 2>/dev/null || true")
        log("[ACTION] Forced chrony step (large skew)")
    else:
        log(f"[INFO] Skew {abs_skew:,} ns outside actionable band")


# ---------------------------------------------------------------------------
# Ultrafast loop
# ---------------------------------------------------------------------------

_stop_event: asyncio.Event | None = None


def _handle_signal(sig: int, *_) -> None:
    log(f"[SIGNAL] Received {signal.Signals(sig).name} — stopping loop...")
    if _stop_event:
        _stop_event.set()


async def ultrafast_loop(threshold_ns: int, interval: int) -> None:
    global _stop_event
    _stop_event = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (OSError, ValueError):
            pass  # some signals unavailable on Windows

    log(f"[ULTRAFAST] Continuous polling — interval={interval}s threshold={threshold_ns:,} ns")
    while not _stop_event.is_set():
        if IS_WINDOWS:
            await windows_drift_correct(threshold_ns)
        else:
            await unix_drift_correct(threshold_ns)
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    log("[ULTRAFAST] Loop exited cleanly.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    args = parse_args()
    mode = args.mode
    cfg  = MODE_CONFIG[mode]

    pools = ([args.pool] + DEFAULT_POOLS) if args.pool else DEFAULT_POOLS

    log(f"NTPsecDispatcher starting | platform={platform.system()} mode={mode}")

    if IS_WINDOWS:
        await windows_configure(pools)
        await windows_drift_correct(cfg["threshold_ns"])
        await windows_install_service(mode)
    else:
        await unix_configure(pools, mode)
        await unix_drift_correct(cfg["threshold_ns"])

    if mode == "ultrafast":
        await ultrafast_loop(cfg["threshold_ns"], cfg["interval"])
    else:
        log(f"Dispatcher complete. Next run in {cfg['interval']}s (if scheduled).")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[EXIT] Interrupted.")
        sys.exit(0)
