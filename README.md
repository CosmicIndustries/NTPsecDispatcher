# NTPsecDispatcher
a launcher/service launcher for sycnchronising windows or linux with a secure NTP server.

🕒 TimeSync Agent

Cross-platform, high-precision NTP synchronization agent with auto-fallback and nanosecond telemetry.

✨ Features

Cross-platform: Supports Windows, Linux, macOS

Dynamic pool selection: Automatically picks the first reachable NTP or NTPsec pool

High-precision drift correction: Nanosecond-level skew measurement & adjustment

Modes:

fast – checks every 60 s

ultrafast – continuous polling with nanosecond precision

lazy – checks every 30 minutes

Memoization: Stores last skew in JSON to avoid unnecessary corrections

Telemetry: Logs skew, service status, and NTP peer info to status.log

Resilience: Falls back to alternative pools if DNS or server fails

SYSTEM service integration (Windows): Automatically installs via NSSM

Cross-platform scheduling: Uses Scheduled Tasks (Windows) or cron/systemd timers (Unix)

🛠️ Installation
Windows

Install Python 3.9+

Install NSSM: https://nssm.cc/download
