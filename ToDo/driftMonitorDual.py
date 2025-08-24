
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dispatchTUI.py
--------------
Real-time nanosecond skew monitor + corrective agent with ASCII TUI.
Supports Windows (w32tm) and Unix (chrony / ntpsec) measurement + correction.
Modes: ultrafast (continuous TUI), fast/lazy (one-shot correction).
Author: user + ChatGPT
"""

from __future__ import annotations
import asyncio
import os
import sys
import platform
import json
import shutil
from datetime import datetime
from collections import deque
import argparse
from typing import Optional

# ---------------------------
# CLI argument parsing
# ---------------------------
parser = argparse.ArgumentParser(description="Time Drift TUI + corrective agent")
parser.add_argument("--mode", choices=["fast", "ultrafast", "lazy"], default="fast",
                    help="fast = one-shot; ultrafast = continuous TUI; lazy = infrequent checks")
parser.add_argument("--pool", type=str, default=None, help="(optional) prefer this pool")
args = parser.parse_args()
MODE = args.mode
FORCED_POOL = args.pool

# ---------------------------
# Paths, memo, logging
# ---------------------------
IS_WINDOWS = platform.system() == "Windows"
LOG_DIR = (r"C:\ProgramData\TimeSync" if IS_WINDOWS else "/var/log/time-sync")
os.makedirs(LOG_DIR, exist_ok=True)
STATUS_FILE = os.path.join(LOG_DIR, "status.log")
MEMO_FILE = os.path.join(LOG_DIR, "memo.json")

# ---------------------------
# Mode settings
# ---------------------------
if MODE == "ultrafast":
    CHECK_INTERVAL = 1              # seconds between checks in TUI
    PRECISION_THRESHOLD_NS = 1_000  # 1 micro? (we'll treat as 1,000 ns)
elif MODE == "lazy":
    CHECK_INTERVAL = 300            # 5 minutes
    PRECISION_THRESHOLD_NS = 1_000_000  # 1 ms
else:  # fast
    CHECK_INTERVAL = 60             # 1 minute
    PRECISION_THRESHOLD_NS = 100_000  # 0.1 ms

# ---------------------------
# Graph / history
# ---------------------------
HISTORY_LEN = 80                    # number of samples to show horizontally
skew_history: deque[int] = deque(maxlen=HISTORY_LEN)

# ---------------------------
# Helpers
# ---------------------------

def now_ts() -> str:
    """Return current UTC timestamp."""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"

def write_status_log(line: str) -> None:
    """Append a line to status log (ensures directory exists)."""
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(STATUS_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def log(msg: str) -> None:
    """Console + status log."""
    line = f"[{now_ts()}] {msg}"
    print(line)
    write_status_log(line)

# ---------------------------
# Async shell runner
# ---------------------------
async def run_cmd(cmd: str, timeout: Optional[int] = 15) -> str:
    """
    Run a shell command and return stdout as text.
    Captures stderr and logs it (not returned).
    """
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out_s = out.decode(errors="ignore").strip()
        err_s = err.decode(errors="ignore").strip()
        if err_s:
            log(f"[CMD-ERR] {cmd} => {err_s}")
        return out_s
    except asyncio.TimeoutError:
        log(f"[CMD-ERR] Timeout running: {cmd}")
        return ""
    except Exception as e:
        log(f"[CMD-ERR] Exception running: {cmd} => {e}")
        return ""

# ---------------------------
# Memoization helpers
# ---------------------------
def load_memo() -> dict:
    """Load memo.json (last_skew_ns), or return empty dict."""
    try:
        if os.path.exists(MEMO_FILE):
            with open(MEMO_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        log(f"[WARN] Could not load memo: {e}")
    return {}

def save_memo(d: dict) -> None:
    """Write memo.json atomically."""
    try:
        tmp = MEMO_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, MEMO_FILE)
    except Exception as e:
        log(f"[WARN] Could not save memo: {e}")

memo = load_memo()

# ---------------------------
# Skew measurement functions
# ---------------------------
async def measure_skew_windows_stripchart() -> Optional[int]:
    """
    Use w32tm /stripchart to get a single sample in seconds and convert to ns.
    Returns nanoseconds (int) or None.
    """
    # Use time.windows.com as a stable reference. /dataonly gives lines like "15:10:00, -0.0090668s"
    out = await run_cmd('w32tm /stripchart /computer:time.windows.com /dataonly /samples:1', timeout=10)
    if not out:
        return None
    # Find last comma line
    lines = [l.strip() for l in out.splitlines() if "," in l]
    if not lines:
        # sometimes stripchart prints no data
        return None
    last = lines[-1]
    try:
        # Split on comma, second field is like " -0.0090668s"
        parts = last.split(",", 1)[1].strip()
        secs_text = parts.rstrip("s").strip()
        skew_s = float(secs_text)
        skew_ns = int(skew_s * 1e9)
        return skew_ns
    except Exception:
        return None

async def measure_skew_windows_status() -> Optional[int]:
    """
    Fallback parser that inspects 'w32tm /query /status' output for an 'Offset' line.
    Offset lines vary by locale; we attempt to find a numeric seconds value.
    """
    out = await run_cmd("w32tm /query /status", timeout=6)
    if not out:
        return None
    for line in out.splitlines():
        if "Offset" in line or "Local Clock" in line:
            # Find any float in the line
            import re
            m = re.search(r"([-+]?\d+\.\d+)\s*s", line)
            if m:
                try:
                    skew_s = float(m.group(1))
                    return int(skew_s * 1e9)
                except ValueError:
                    continue
    return None

async def measure_skew_windows() -> Optional[int]:
    """Try stripchart first, fallback to status parsing."""
    ns = await measure_skew_windows_stripchart()
    if ns is not None:
        return ns
    return await measure_skew_windows_status()

async def measure_skew_chrony() -> Optional[int]:
    """
    Run 'chronyc tracking' and parse the 'Last offset' field.
    Returns nanoseconds (int) or None.
    Example line: 'Last offset     : 0.000056 seconds'
    """
    out = await run_cmd("chronyc tracking", timeout=6)
    if not out:
        return None
    for line in out.splitlines():
        if "Last offset" in line:
            try:
                # Split at colon and take first numeric token
                value = line.split(":", 1)[1].strip().split()[0]
                skew_s = float(value)
                return int(skew_s * 1e9)
            except Exception:
                return None
    return None

async def measure_skew_ntpd() -> Optional[int]:
    """
    If ntpd/ntpsec tools are present but not chrony, we could use 'ntpq -c rv' or similar.
    This function tries 'ntpq -c rv' and looks for "offset=" key.
    """
    out = await run_cmd("ntpq -c rv", timeout=6)
    if not out:
        return None
    # parse offset=VALUE
    for token in out.replace(",", " ").split():
        if token.startswith("offset="):
            try:
                skew_s = float(token.split("=", 1)[1])
                return int(skew_s * 1e9)
            except Exception:
                return None
    return None

# ---------------------------
# Drift correction actions
# ---------------------------
async def apply_windows_small() -> None:
    """Apply small incremental adjustment on Windows."""
    # /nowait does not block; it asks service to adjust gradually
    await run_cmd("w32tm /resync /nowait")
    log("[ACTION] w32tm /resync /nowait issued (small adjustment)")

async def apply_windows_force() -> None:
    """Apply forced step on Windows."""
    await run_cmd("w32tm /resync /force")
    log("[ACTION] w32tm /resync /force issued (forced step)")

async def apply_chrony_small() -> None:
    """Apply a small precise chrony makestep (0.001 = 1 ms, but usable for smaller adjustments)."""
    await run_cmd("chronyc makestep 0.001 3")
    log("[ACTION] chronyc makestep 0.001 3 issued (small adjustment)")

async def apply_chrony_force() -> None:
    """Apply forced chrony makestep."""
    await run_cmd("chronyc makestep")
    log("[ACTION] chronyc makestep issued (forced step)")

# ---------------------------
# High-level drift-check + correction
# ---------------------------
async def drift_check_and_correct_windows() -> Optional[int]:
    """
    Measure skew, decide action, memoize, and append to skew history.
    Returns skew_ns measured (or None).
    """
    skew_ns = await measure_skew_windows()
    if skew_ns is None:
        log("[WARN] Could not measure skew on Windows")
        return None

    last = memo.get("last_skew_ns", 0)
    memo["last_skew_ns"] = skew_ns
    save_memo(memo := memo)  # persist

    skew_history.append(skew_ns)
    log(f"[MEASURE] Windows skew = {skew_ns} ns (previous {last} ns)")

    abs_skew = abs(skew_ns)
    # Small skew -> between configured precision threshold and 1 ms (1_000_000 ns)
    if PRECISION_THRESHOLD_NS < abs_skew < 1_000_000:
        await apply_windows_small()
    # Large skew -> between 100 ms and 1 s
    elif 100_000_000 < abs_skew < 1_000_000_000:
        await apply_windows_force()
    else:
        log("[DECISION] No adjustment required for Windows (skew within negligible range)")
    return skew_ns

async def drift_check_and_correct_unix() -> Optional[int]:
    """
    Try chrony measurement first; if not present, try ntpq-style. Apply corrections similarly.
    """
    # prefer chrony
    skew_ns = None
    # check availability of commands
    chrony_path = shutil.which("chronyc")
    if chrony_path:
        skew_ns = await measure_skew_chrony()
        tool = "chrony"
    else:
        # try ntpq
        ntpq_path = shutil.which("ntpq")
        if ntpq_path:
            skew_ns = await measure_skew_ntpd()
            tool = "ntpd"
        else:
            log("[WARN] No chronyc or ntpq available to measure skew")
            return None

    if skew_ns is None:
        log("[WARN] Could not parse skew from Unix tool output")
        return None

    last = memo.get("last_skew_ns", 0)
    memo["last_skew_ns"] = skew_ns
    save_memo(memo)

    skew_history.append(skew_ns)
    log(f"[MEASURE] {tool} skew = {skew_ns} ns (previous {last} ns)")

    abs_skew = abs(skew_ns)
    if PRECISION_THRESHOLD_NS < abs_skew < 1_000_000:
        if tool == "chrony":
            await apply_chrony_small()
        else:
            # ntpd small change (ntpdate/ntpd method could be custom)
            await run_cmd("sudo ntpdate -u pool.ntp.org || true")
            log("[ACTION] ntpdate attempted for small skew")
    elif 100_000_000 < abs_skew < 1_000_000_000:
        if tool == "chrony":
            await apply_chrony_force()
        else:
            await run_cmd("sudo ntpdate -u pool.ntp.org || true")
            log("[ACTION] ntpdate attempted for large skew")
    else:
        log("[DECISION] No adjustment required for Unix (skew within negligible range)")
    return skew_ns

# ---------------------------
# ASCII Graph rendering
# ---------------------------
def render_graph() -> str:
    """
    Render the skew history as a horizontal graph using block characters.
    Center column is zero; right = positive offset, left = negative.
    """
    width = shutil.get_terminal_size((120, 30)).columns
    # Reserve margin for labels; use only inner width for plotting
    plot_w = max(20, width - 20)
    center = plot_w // 2

    # Determine scale: map max abs skew in history to half-plot width
    max_abs = max((abs(x) for x in skew_history), default=1)
    scale = max_abs / (center - 1) if max_abs > 0 else 1

    lines = []
    # Top header row with numeric scale (approx)
    header = f"Scale: ±{int(max_abs)} ns (center=0)"
    lines.append(header)
    # For each sample produce a single-line bar
    for val in list(skew_history):
        # compute offset in columns
        if scale <= 0:
            offs = 0
        else:
            offs = int(round(val / scale))
        # clamp
        left = center + min(offs, 0)
        right = center + max(offs, 0)

        row = []
        for c in range(plot_w):
            if c == center:
                # center axis
                row.append("¦")
            elif left <= c < center and offs < 0:
                row.append("?")   # left block (negative)
            elif center < c <= right and offs > 0:
                row.append("¦")   # right block (positive)
            else:
                row.append(" ")
        # Prepend timestamp or short index for readability
        lines.append("".join(row))
    # Keep only last ~20 lines to avoid too long output in small terminals
    max_lines = min(40, len(lines))
    return "\n".join(lines[-max_lines:])

def draw_tui() -> None:
    """Clear screen and draw TUI (header + graph + stats)."""
    os.system("cls" if IS_WINDOWS else "clear")
    # Header box
    print("+" + "-" * 46 + "+")
    print("¦   Time Drift Monitor (Nanosecond TUI)       ¦")
    print("+" + "-" * 46 + "+\n")
    # Graph
    print(render_graph())
    # Footer stats
    last = skew_history[-1] if skew_history else 0
    print("\nLegend: center¦=0 ns, right=positive (¦), left=negative (?)")
    print(f"Mode: {MODE} | Samples: {len(skew_history)} | Last skew: {last} ns")
    print(f"Log: {STATUS_FILE} | Memo: {MEMO_FILE}")
    print("Press Ctrl-C to exit (if running interactively).")

# ---------------------------
# Main ultrafast loop
# ---------------------------
async def ultrafast_loop() -> None:
    """Continuously measure, correct, and display TUI."""
    log("[ULTRAFAST] Entering continuous monitoring loop...")
    while True:
        if IS_WINDOWS:
            await drift_check_and_correct_windows()
        else:
            await drift_check_and_correct_unix()
        draw_tui()
        await asyncio.sleep(CHECK_INTERVAL)

# ---------------------------
# One-shot runner (fast/lazy modes)
# ---------------------------
async def one_shot_run() -> None:
    """Run a single measure/correct/log pass (non-TUI mode)."""
    log(f"[ONE-SHOT] Running one-shot check (mode={MODE})")
    if IS_WINDOWS:
        await drift_check_and_correct_windows()
    else:
        await drift_check_and_correct_unix()
    log("[ONE-SHOT] Completed")

# ---------------------------
# Entrypoint
# ---------------------------
async def main() -> None:
    """Main dispatcher entry — chooses ultrafast loop or single run."""
    log(f"Starting dispatchTUI on {platform.system()} | Mode={MODE}")
    # Ensure history prepopulated (avoid empty graph)
    if not skew_history:
        for _ in range(4):
            skew_history.append(0)
    if MODE == "ultrafast":
        await ultrafast_loop()
    else:
        await one_shot_run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[EXIT] Interrupted by user.")
        sys.exit(0)
```

