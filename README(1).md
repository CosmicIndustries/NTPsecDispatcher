# NTPsecDispatcher

<img src="images/hero_image.svg" alt="NTPsecDispatcher" width="800">

> Cross-platform, high-precision NTP synchronization agent with auto-fallback and nanosecond telemetry.

[![CI](https://github.com/CosmicIndustries/NTPsecDispatcher/actions/workflows/ci.yml/badge.svg)](https://github.com/CosmicIndustries/NTPsecDispatcher/actions/workflows/ci.yml)
[![License: Unlicense](https://img.shields.io/badge/license-Unlicense-blue.svg)](LICENSE)

---

## Features

- **Cross-platform** — Windows (W32Time + NSSM), Linux, macOS
- **Dynamic pool selection** — picks the first reachable NTP/NTPsec pool, falls back automatically
- **Nanosecond drift correction** — incremental resync for small skews, forced step for large ones
- **Three modes** — `fast` (60 s), `ultrafast` (5 s continuous), `lazy` (30 min)
- **Memoization** — skips unnecessary corrections by comparing against the last measured skew
- **Telemetry** — logs skew, service status, and NTP peer info to `status.log`
- **NTS support** — secure Network Time Security when chrony ≥ 4.0 is detected
- **Live drift monitor** — ASCII TUI showing real-time skew history (`driftMonitor.py`)
- **Service integration** — Windows NSSM service or scheduled task; Linux systemd timer

---

## How it works

Two entry points, one for each role:

| Script | Role | Requires root? |
|---|---|---|
| `time-sync-agent.sh` / `.bat` | System configurator — writes daemon config, restarts chrony/W32Time | Yes |
| `dispatcher.py` | Drift monitor/corrector — measures skew and applies corrections | No (skips config if not root) |

Run the shell script once to configure the daemon, then run `dispatcher.py` on a schedule for ongoing drift correction.

---

## Installation

### Linux / macOS

**Requirements:** Python 3.9+, and one of: `chrony` (recommended), `ntpsec`, or `systemd-timesyncd`

```bash
# 1. Install chrony (if not already present)
sudo apt-get install -y chrony        # Debian/Ubuntu
sudo dnf install -y chrony            # Fedora/RHEL

# 2. Clone the repo
git clone https://github.com/CosmicIndustries/NTPsecDispatcher.git
cd NTPsecDispatcher
chmod +x time-sync-agent.sh

# 3. Configure the NTP daemon (root required — run once)
sudo ./time-sync-agent.sh

# 4. Run the drift dispatcher (no root needed)
python3 dispatcher.py --mode=fast
```

Optional: schedule drift correction with cron:

```bash
# Every 5 minutes
*/5 * * * * /usr/bin/python3 /path/to/dispatcher.py --mode=fast
```

Or as a systemd service — the shell script installs a telemetry timer automatically.

---

### Windows

**Requirements:** Python 3.9+, optional [NSSM](https://nssm.cc/download) for service install

```bat
REM 1. Clone or download this repository
REM 2. Run the launcher (picks a reachable pool automatically)
time-sync-agent.bat

REM 3. Install as a SYSTEM service via NSSM (optional)
REM    Place nssm.exe at C:\nssm\nssm.exe first
python dispatcher.py --mode=fast
```

The launcher auto-detects a reachable pool and passes it to the dispatcher. NSSM service installation is handled automatically if `C:\nssm\nssm.exe` is present; otherwise a scheduled task is created as a fallback.

---

## Usage

```bash
# One-shot fast check (default)
python3 dispatcher.py --mode=fast

# Continuous nanosecond-level polling (daemon mode)
python3 dispatcher.py --mode=ultrafast

# Low-frequency check (laptops, infrequent correction)
python3 dispatcher.py --mode=lazy

# Force a specific pool
python3 dispatcher.py --mode=fast --pool=pool.chrony.eu

# Real-time ASCII drift monitor
python3 ToDo/driftMonitor.py --mode=ultrafast
```

`dispatchService.py` is a backward-compatible shim — existing NSSM or scheduled task entries pointing at it continue to work.

---

## Modes

| Mode | Interval | Correction threshold |
|---|---|---|
| `fast` | 60 s | > 0.1 ms |
| `ultrafast` | 5 s | > 1 µs |
| `lazy` | 30 min | > 1 ms |

---

## Logs and state

| Platform | Log | Memo |
|---|---|---|
| Linux/macOS (root) | `/var/log/time-sync/status.log` | `/var/log/time-sync/memo.json` |
| Linux/macOS (user) | `~/.local/state/ntpsec/status.log` | `~/.local/state/ntpsec/memo.json` |
| Windows | `%ProgramData%\TimeSync\status.log` | `%ProgramData%\TimeSync\memo.json` |

The memo file stores the last measured skew in nanoseconds across runs to avoid redundant corrections.

---

## Security

- Incremental adjustments for small skews — no abrupt clock jumps
- Forced step only for skews between 100 ms and 1 s
- NTS (Network Time Security) enabled automatically when chrony ≥ 4.0 is detected
- Cache file written with `chmod 600` — not world-readable
- Config written atomically via temp-file rename — safe against crash mid-write
- Windows registry and service commands fail gracefully and are fully logged

See [SECURITY.md](SECURITY.md) for vulnerability reporting.

---

## Development

- Python 3.9+ required, no third-party dependencies
- `dispatcher.py` is the single source of truth — `dispatchService.py` is a shim
- `asyncio` throughout — no blocking calls, all subprocesses have a 15 s timeout
- Pull requests welcome — maintain cross-platform compatibility

---

## Docs

Full setup reference (Windows registry steps, chrony config details):
[Docs/Fuscus.md](Docs/Fuscus.md)

---

## License

[Unlicense](LICENSE) — public domain.
