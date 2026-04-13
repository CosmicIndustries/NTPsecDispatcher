#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
driftMonitor.py — real-time nanosecond drift graph + corrective agent.

Renders an ASCII TUI showing clock skew history over time.
Applies corrective actions automatically in ultrafast mode.

Usage:
    python driftMonitor.py [--mode=fast|ultrafast|lazy] [--pool=HOST]

Requires: dispatcher.py in the same directory (shares run_cmd, measure logic).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import platform
import shutil
import signal
import sys
from collections import deque
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Re-use shared infrastructure from dispatcher.py
# ---------------------------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

try:
    from dispatcher import (
        run_cmd,
        log,
        load_memo,
        save_memo,
        windows_measure_skew,
        windows_drift_correct,
        unix_measure_skew,
        unix_drift_correct,
        MODE_CONFIG,
        IS_WINDOWS,
    )
except ImportError as exc:
    sys.exit(f"[ERROR] Cannot import dispatcher.py: {exc}\nEnsure driftMonitor.py and dispatcher.py are in the same directory.")

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Real-time NTP drift monitor TUI")
    p.add_argument("--mode", choices=["fast", "ultrafast", "lazy"], default="ultrafast")
    p.add_argument("--pool", default=None, metavar="HOST")
    return p.parse_args()

# ---------------------------------------------------------------------------
# Skew history
# ---------------------------------------------------------------------------

HISTORY_LEN = 80
skew_history: deque[int] = deque(maxlen=HISTORY_LEN)

# ---------------------------------------------------------------------------
# TUI rendering
# ---------------------------------------------------------------------------

def _term_width() -> int:
    return max(40, shutil.get_terminal_size((100, 24)).columns)


def _human_ns(ns: int) -> str:
    """Format nanoseconds as a human-readable string."""
    abs_ns = abs(ns)
    sign = "+" if ns >= 0 else "-"
    if abs_ns >= 1_000_000_000:
        return f"{sign}{abs_ns / 1e9:.3f} s"
    if abs_ns >= 1_000_000:
        return f"{sign}{abs_ns / 1e6:.3f} ms"
    if abs_ns >= 1_000:
        return f"{sign}{abs_ns / 1e3:.3f} µs"
    return f"{sign}{abs_ns} ns"


def render_graph(width: int) -> list[str]:
    """
    Return list of lines showing skew history as a centered bar chart.
    Positive skew → right of center (▶), negative → left (◀).
    """
    plot_w = max(20, width - 22)   # leave room for labels
    center  = plot_w // 2
    max_abs = max((abs(x) for x in skew_history), default=1)
    scale   = max_abs / max(center - 2, 1)

    lines: list[str] = []
    lines.append(f"  {'─' * plot_w}")
    lines.append(f"  {'◀ negative':^{center}}{'│':1}{'positive ▶':^{center}}")
    lines.append(f"  {'─' * center}┼{'─' * center}")

    for val in list(skew_history):
        offs = int(round(val / scale)) if scale > 0 else 0
        offs = max(-center + 1, min(center - 1, offs))

        row = [" "] * plot_w
        row[center] = "│"

        if offs > 0:
            for c in range(center, center + offs):
                row[c] = "▶"
        elif offs < 0:
            for c in range(center + offs, center):
                row[c] = "◀"

        label = _human_ns(val).rjust(10)
        lines.append(f"{label}  {''.join(row)}")

    lines.append(f"  {'─' * center}┼{'─' * center}")
    return lines


def draw_tui(mode: str, interval: int) -> None:
    width = _term_width()
    os.system("cls" if IS_WINDOWS else "clear")

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    title = f"NTPsecDispatcher — Drift Monitor  [{mode.upper()} | {interval}s interval | {ts}]"
    print(title[:width])
    print("═" * min(len(title), width))
    print()

    if skew_history:
        for line in render_graph(width):
            print(line)
        last = skew_history[-1]
        last_str  = _human_ns(last)
        drift_dir = "FAST" if last > 0 else ("SLOW" if last < 0 else "SYNC")
        print()
        print(f"  Last: {last_str:>12}  │  Clock is: {drift_dir}")
        print(f"  Samples buffered: {len(skew_history)}/{HISTORY_LEN}")
    else:
        print("  Waiting for first measurement...")

    print()
    print("  Press Ctrl-C to exit.")


# ---------------------------------------------------------------------------
# Main monitoring loop
# ---------------------------------------------------------------------------

_stop_event: asyncio.Event | None = None


def _handle_signal(*_) -> None:
    if _stop_event:
        _stop_event.set()


async def monitor_loop(mode: str) -> None:
    global _stop_event
    _stop_event = asyncio.Event()

    cfg = MODE_CONFIG[mode]
    interval    = cfg["interval"]
    threshold   = cfg["threshold_ns"]

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except (OSError, ValueError):
            pass

    # Pre-fill with zeros so graph renders immediately
    skew_history.extend([0] * min(4, HISTORY_LEN))

    log(f"[MONITOR] Starting | mode={mode} interval={interval}s")

    while not _stop_event.is_set():
        # Measure
        if IS_WINDOWS:
            skew_ns = await windows_measure_skew()
        else:
            skew_ns = await unix_measure_skew()

        if skew_ns is not None:
            skew_history.append(skew_ns)

            # Correct if in ultrafast (continuous) mode
            if mode == "ultrafast":
                if IS_WINDOWS:
                    await windows_drift_correct(threshold)
                else:
                    await unix_drift_correct(threshold)

        draw_tui(mode, interval)

        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

    print("\n[MONITOR] Exited.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    args = parse_args()
    await monitor_loop(args.mode)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[EXIT] Interrupted.")
        sys.exit(0)
