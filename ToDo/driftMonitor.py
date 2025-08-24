
#!/usr/bin/env python3
"""
dispatchTUI.py – High-precision time sync dispatcher with ASCII TUI drift graph.
Author: Dinadeyohvsgi + ChatGPT
"""

import os, sys, platform, json, asyncio, time, shutil
from datetime import datetime
from collections import deque

# -----------------------------
# Config / Memoization
# -----------------------------
LOG_DIR = r"C:\ProgramData\TimeSync" if platform.system() == "Windows" else "/var/log/time-sync"
MEMO_FILE = os.path.join(LOG_DIR, "memo.json")
STATUS_FILE = os.path.join(LOG_DIR, "status.log")

MODE = "fast"
if "--mode=ultrafast" in sys.argv: MODE = "ultrafast"
elif "--mode=lazy" in sys.argv: MODE = "lazy"

if MODE == "ultrafast": CHECK_INTERVAL = 1
elif MODE == "lazy":    CHECK_INTERVAL = 300
else:                   CHECK_INTERVAL = 30

# keep last N skew samples for graphing
HISTORY_LEN = 50
skew_history = deque(maxlen=HISTORY_LEN)

# -----------------------------
# Logging helper
# -----------------------------
def log(msg):
    ts = datetime.utcnow().strftime("[%Y-%m-%d %H:%M:%S UTC]")
    line = f"{ts} {msg}"
    print(line)
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(STATUS_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")

# -----------------------------
# Memoization helpers
# -----------------------------
def load_memo():
    if os.path.exists(MEMO_FILE):
        try:
            return json.load(open(MEMO_FILE))
        except: return {}
    return {}

def save_memo(data):
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(MEMO_FILE, "w") as f:
        json.dump(data, f)

memo = load_memo()

# -----------------------------
# Command runner
# -----------------------------
async def run_cmd(cmd):
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        return out.decode(errors="ignore").strip()
    except Exception as e:
        return str(e)

# -----------------------------
# Skew measurement + correction
# -----------------------------
async def measure_skew_windows():
    out = await run_cmd("w32tm /query /status")
    ns = 0
    for line in out.splitlines():
        if "Offset" in line:
            # e.g., "Offset: -0.0098767s"
            try:
                parts = line.split(":")[1].strip()
                if "s" in parts: parts = parts.replace("s","")
                offset_sec = float(parts)
                ns = int(offset_sec * 1e9)
            except: pass
    return ns

async def drift_correction_windows():
    skew_ns = await measure_skew_windows()
    last_skew = memo.get("last_skew", 0)
    skew_history.append(skew_ns)

    log(f"[INFO] Current skew: {skew_ns} ns (last: {last_skew} ns)")

    # decision logic
    if abs(skew_ns) < 1_000_000:   # <1 ms
        log("[INFO] Small skew → gradual resync")
        await run_cmd("w32tm /resync /nowait")
    elif abs(skew_ns) > 100_000_000:  # >100 ms
        log("[WARN] Large skew → forced step")
        await run_cmd("w32tm /resync /force")
    else:
        log("[INFO] Skew negligible; no action")

    memo["last_skew"] = skew_ns
    save_memo(memo)

# -----------------------------
# ASCII Graph TUI
# -----------------------------
def render_graph():
    cols = shutil.get_terminal_size((80, 20)).columns
    max_val = max([abs(x) for x in skew_history] + [1])
    scale = max_val / (cols//2)  # center graph

    lines = []
    for val in skew_history:
        offset = int(val / scale) if scale > 0 else 0
        center = cols//2
        line = [" "] * cols
        line[center] = "│"
        if offset > 0:
            for i in range(center, min(center+offset, cols-1)):
                line[i] = "▄"
        elif offset < 0:
            for i in range(center+offset, center):
                line[i] = "▀"
        lines.append("".join(line))
    return "\n".join(lines)

def draw_tui():
    os.system("cls" if platform.system()=="Windows" else "clear")
    print("╔════════════════════════════════════════════╗")
    print("║   Time Drift Monitor (Nanosecond TUI)      ║")
    print("╚════════════════════════════════════════════╝\n")
    print(render_graph())
    print("\nLegend: center│=0 ns, right=positive skew (▄), left=negative skew (▀)")
    print(f"Samples: {len(skew_history)} | Last skew: {skew_history[-1] if skew_history else 0} ns")

# -----------------------------
# Ultrafast loop
# -----------------------------
async def ultrafast_loop():
    while True:
        if platform.system() == "Windows":
            await drift_correction_windows()
        else:
            # (similar chrony/ntpsec implementation can be plugged in)
            pass

        draw_tui()
        await asyncio.sleep(CHECK_INTERVAL)

# -----------------------------
# Main
# -----------------------------
async def main():
    log(f"Dispatcher start on {platform.system()} | Mode={MODE}")
    if MODE == "ultrafast":
        await ultrafast_loop()
    else:
        # one-shot correction
        if platform.system() == "Windows":
            await drift_correction_windows()
        log("Dispatcher complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[EXIT] Stopped by user.")

