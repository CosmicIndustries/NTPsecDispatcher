# NTPsecDispatcher
<img src="images/hero_image.svg" alt="NTPsecure Hero" width="800">
a launcher/service launcher for sycnchronising windows or linux with a secure NTP server.


<div class="flex-1">
    <a href="/CosmicIndustries/NTPsecDispatcher/releases/latest">
        <span>Latest</span>
    </a>
</div>


Docs
https://github.com/CosmicIndustries/NTPsecDispatcher/blob/main/Docs/Fuscus.md
---

# 🕒 TimeSync Agent

**Cross-platform, high-precision NTP synchronization agent with auto-fallback and nanosecond telemetry.**

---

## ✨ Features

* **Cross-platform:** Supports **Windows**, **Linux**, **macOS**
* **Dynamic pool selection:** Automatically picks the first reachable NTP or NTPsec pool
* **High-precision drift correction:** Nanosecond-level skew measurement & adjustment
* **Modes:**

  * `fast` – checks every **60 s**
  * `ultrafast` – **continuous polling** with nanosecond precision
  * `lazy` – checks every **30 minutes**
* **Memoization:** Stores last skew in JSON to **avoid unnecessary corrections**
* **Telemetry:** Logs skew, service status, and NTP peer info to `status.log`
* **Resilience:** Falls back to alternative pools if DNS or server fails
* **SYSTEM service integration (Windows):** Automatically installs via **NSSM**
* **Cross-platform scheduling:** Uses **Scheduled Tasks** (Windows) or **cron/systemd timers** (Unix)

---

## 🛠️ Installation

### Windows

1. Install **Python 3.9+**
2. Install **NSSM**: [https://nssm.cc/download](https://nssm.cc/download)
3. Clone or download this repository
4. Edit `time-sync-agent.bat` with your **Python path** and **dispatcher path**
5. Run manually once to verify:

```bat
time-sync-agent.bat
```

6. To install as a SYSTEM service (via NSSM):

```bat
python -m dispatchService --mode=fast
```

---

### Linux / macOS

1. Install **Python 3.9+**
2. Install **chrony** or **NTPsec**
3. Clone this repository
4. Run dispatcher manually:

```bash
python3 dispatcher.py --mode=fast
```

5. Optional: schedule with `cron` or `systemd`:

```bash
# Every 5 minutes
*/5 * * * * /usr/bin/python3 /path/to/dispatcher.py --mode=fast
```

---

## 🚀 Usage

```bash
# Default fast mode
python -m dispatchService --mode=fast

# Ultrafast continuous nanosecond polling
python -m dispatchService --mode=ultrafast

# Lazy mode (low frequency)
python -m dispatchService --mode=lazy

# Optional: force a specific pool
python -m dispatchService --mode=fast --pool=pool.chrony.eu
```

---

## ⚙️ Configuration

* **Pools:** Default dispatcher list includes NTP, NTPsec, Cloudflare, Google pools
* **Modes:** Adjust `CHECK_INTERVAL` and `PRECISION_THRESHOLD_NS` per mode in `dispatcher.py`
* **Logging:** Telemetry stored in:

  * Windows: `%ProgramData%\TimeSync\status.log`
  * Unix: `/var/log/time-sync/status.log`
* **Memoization:** Last skew stored in `memo.json` alongside logs

---

## 🔒 Safety & Security

* **Incremental adjustments** prevent large clock jumps for small skews
* **Forced resync** only for large skews (>100 ms)
* Supports **NTPsec** and secure **NTS** where available
* Windows registry & service commands **fail gracefully** and are fully logged

---

## 💡 Development / Contribution

* **Python 3.9+** required
* Uses **asyncio** for concurrency and live polling
* BAT wrapper handles Windows service bootstrap
* Pull requests welcome — maintain **cross-platform compatibility**

---

## 📄 License

**UnLicense** — see `LICENSE` file
