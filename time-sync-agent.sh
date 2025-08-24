#!/usr/bin/env bash
# ============================================================
# Time Sync Agent (Linux/macOS)
# - Prefers chrony; falls back to ntpsec/ntpd; then timesyncd
# - NTS support (Cloudflare/Google) if chrony supports 'nts'
# - Memoization (/var/cache/time-sync/config.cache)
# - Telemetry to /var/log/time-sync/status.log (nanosecond)
# - Hardened pool fallbacks + DNS warm-up + resilience
# - Precision modes: --mode=fast | --mode=safe (default: safe)
# - Optional: pass pool override as $1
# - Optional: --install-ntpsec (attempt package install)
# ============================================================

set -euo pipefail
IFS=$'\n\t'

POOL_ARG="${1:-}"
MODE="safe"
for arg in "$@"; do
  case "$arg" in
    --mode=*) MODE="${arg#*=}";;
    --install-ntpsec) INSTALL_NTPSEC=1;;
  esac
done
[[ "$MODE" = "fast" || "$MODE" = "safe" ]] || MODE="safe"
INSTALL_NTPSEC="${INSTALL_NTPSEC:-0}"

if [[ $EUID -ne 0 ]]; then
  echo "[ERROR] Please run as root (sudo)."
  exit 1
fi

CACHE_DIR="${TIMESYNC_CACHE:-/var/cache/time-sync}"
CACHE_FILE="$CACHE_DIR/config.cache"
LOG_DIR="/var/log/time-sync"
LOG_FILE="$LOG_DIR/status.log"
CACHE_TTL_SECONDS=604800

DEFAULT_POOL="pool.chrony.eu"
FALLBACKS=("time.cloudflare.com" "time.google.com" "pool.ntp.org")

ns_ts() { date -u +"%Y-%m-%dT%H:%M:%S.%N" | sed 's/\([0-9]\{9\}\).*/\1Z/'; }
log() { mkdir -p "$LOG_DIR" "$CACHE_DIR"; printf "[%s] %s\n" "$(ns_ts)" "$*" | tee -a "$LOG_FILE" >/dev/null; }

load_cache() {
  [[ -f "$CACHE_FILE" ]] || return 1
  # shellcheck disable=SC1090
  source "$CACHE_FILE"
  local now; now=$(date +%s)
  [[ -n "${CACHED_AT:-}" && $(( now - CACHED_AT )) -lt $CACHE_TTL_SECONDS ]] || return 1
  return 0
}

save_cache() {
  mkdir -p "$CACHE_DIR"
  cat >"$CACHE_FILE" <<EOF
CACHED_AT=$(date +%s)
MY_TLD=$MY_TLD
MODE=$MODE
EOF
}

resolve_ok() {
  local host="$1"
  getent ahosts "$host" >/dev/null 2>&1 || dig +short "$host" >/dev/null 2>&1 || host "$host" >/dev/null 2>&1
}

MY_TLD=""
if [[ -n "$POOL_ARG" ]]; then
  MY_TLD="$POOL_ARG"
elif load_cache; then
  :
else
  MY_TLD="$DEFAULT_POOL"
fi

if ! resolve_ok "$MY_TLD"; then
  log "Primary pool '$MY_TLD' not resolvable; trying fallbacks..."
  for p in "${FALLBACKS[@]}"; do
    if resolve_ok "$p"; then
      MY_TLD="$p"
      log "Using fallback pool '$p'"
      break
    fi
  done
fi

if ! resolve_ok "$MY_TLD"; then
  log "[FAIL] No resolvable pools."
  exit 2
fi

# Async DNS warm-up
( dig +short "1.${MY_TLD}" >/dev/null 2>&1 || true ) &
( dig +short "2.${MY_TLD}" >/dev/null 2>&1 || true ) &
( dig +short "3.${MY_TLD}" >/dev/null 2>&1 || true ) &
( dig +short "4.${MY_TLD}" >/dev/null 2>&1 || true ) &

# Precision settings
if [[ "$MODE" = "fast" ]]; then
  MINPOLL=4   # 16s
  MAXPOLL=8   # 256s
  MAKESTEP="0.1 10"
else
  MINPOLL=6   # 64s
  MAXPOLL=10  # 1024s
  MAKESTEP="1.0 3"
fi

# Detect services
HAVE_CHRONY=0; command -v chronyd >/dev/null 2>&1 && HAVE_CHRONY=1
HAVE_NTPD=0;  command -v ntpd    >/dev/null 2>&1 && HAVE_NTPD=1
HAVE_TIMESYNCD=0; systemctl list-unit-files 2>/dev/null | grep -q '^systemd-timesyncd' && HAVE_TIMESYNCD=1

# Optional: install ntpsec if requested
install_ntpsec() {
  if command -v ntpd >/dev/null 2>&1; then return 0; fi
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update && apt-get install -y ntpsec || true
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y ntpsec || true
  elif command -v yum >/dev/null 2>&1; then
    yum install -y ntpsec || true
  elif command -v brew >/dev/null 2>&1; then
    brew install ntpsec || true
  fi
}
if [[ "$INSTALL_NTPSEC" = "1" ]]; then
  log "Attempting ntpsec installation..."
  install_ntpsec
  command -v ntpd >/dev/null 2>&1 && HAVE_NTPD=1
fi

save_cache

# Prefer chrony
if [[ $HAVE_CHRONY -eq 1 ]]; then
  CHRONY_CONF="/etc/chrony/chrony.conf"
  [[ -f /etc/chrony.conf ]] && CHRONY_CONF="/etc/chrony.conf"

  cp -a "$CHRONY_CONF" "${CHRONY_CONF}.bak.$(date +%s)" 2>/dev/null || true
  {
    echo "# Managed by time-sync-agent.sh"
    echo "driftfile /var/lib/chrony/chrony.drift"
    echo "makestep $MAKESTEP"
    echo "rtcsync"
    echo "leapsectz right/UTC"
    echo "logdir /var/log/chrony"
    # Primary pool (non-NTS pools)
    echo "pool 1.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL"
    echo "pool 2.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL"
    echo "pool 3.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL"
    echo "pool 4.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL"
    # Add NTS-capable vendors as secure fallbacks
    echo "server time.cloudflare.com iburst nts minpoll $MINPOLL maxpoll $MAXPOLL"
    echo "server time.google.com    iburst nts minpoll $MINPOLL maxpoll $MAXPOLL"
  } > "$CHRONY_CONF"

  systemctl restart chronyd 2>/dev/null || service chrony restart 2>/dev/null || true

  # Telemetry snapshot
  {
    echo "=== $(ns_ts) chronyc tracking ==="
    chronyc tracking || true
    echo "=== $(ns_ts) chronyc sources -v ==="
    chronyc sources -v || true
  } >> "$LOG_FILE"

  # systemd timer for telemetry (every 15 min)
  if command -v systemctl >/dev/null 2>&1; then
    mkdir -p /etc/systemd/system
    cat >/etc/systemd/system/time-sync-telemetry.service <<EOF
[Unit]
Description=Time Sync Telemetry

[Service]
Type=oneshot
ExecStart=/bin/bash -c '(date; chronyc tracking; chronyc sources -v) >> $LOG_FILE'
EOF
    cat >/etc/systemd/system/time-sync-telemetry.timer <<EOF
[Unit]
Description=Run Time Sync Telemetry every 15 minutes

[Timer]
OnBootSec=2m
OnUnitActiveSec=15m
Unit=time-sync-telemetry.service

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload || true
    systemctl enable --now time-sync-telemetry.timer || true
  fi

  log "[OK] chrony configured. Pool=$MY_TLD Mode=$MODE"
  exit 0
fi

# ntpsec/ntpd path
if [[ $HAVE_NTPD -eq 1 ]]; then
  NTP_CONF="/etc/ntp.conf"
  [[ -d /etc/ntpsec ]] && NTP_CONF="/etc/ntpsec/ntp.conf"

  cp -a "$NTP_CONF" "${NTP_CONF}.bak.$(date +%s)" 2>/dev/null || true
  {
    echo "# Managed by time-sync-agent.sh"
    echo "driftfile /var/lib/ntp/drift"
    echo "tinker panic 0"
    echo "pool 1.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL"
    echo "pool 2.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL"
    echo "pool 3.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL"
    echo "pool 4.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL"
    # ntpsec NTS (if supported by build) can be configured via 'nts' directive in some builds; left conservative here.
    # For strict NTS with ntpsec, additional key/cert/CA directives may be required.
  } > "$NTP_CONF"

  systemctl restart ntpsec 2>/dev/null || systemctl restart ntp 2>/dev/null || service ntpsec restart 2>/dev/null || service ntp restart 2>/dev/null || true

  {
    echo "=== $(ns_ts) ntpq -p ==="
    ntpq -p || true
  } >> "$LOG_FILE"

  # systemd timer for telemetry
  if command -v systemctl >/dev/null 2>&1; then
    mkdir -p /etc/systemd/system
    cat >/etc/systemd/system/time-sync-telemetry.service <<EOF
[Unit]
Description=Time Sync Telemetry

[Service]
Type=oneshot
ExecStart=/bin/bash -c '(date; ntpq -p) >> $LOG_FILE'
EOF
    cat >/etc/systemd/system/time-sync-telemetry.timer <<EOF
[Unit]
Description=Run Time Sync Telemetry every 15 minutes

[Timer]
OnBootSec=2m
OnUnitActiveSec=15m
Unit=time-sync-telemetry.service

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload || true
    systemctl enable --now time-sync-telemetry.timer || true
  fi

  log "[OK] ntp/ntpsec configured. Pool=$MY_TLD Mode=$MODE"
  exit 0
fi

# systemd-timesyncd fallback
if [[ $HAVE_TIMESYNCD -eq 1 ]]; then
  TIMESYNCD_CONF="/etc/systemd/timesyncd.conf"
  cp -a "$TIMESYNCD_CONF" "${TIMESYNCD_CONF}.bak.$(date +%s)" 2>/dev/null || true
  {
    echo "# Managed by time-sync-agent.sh"
    echo "[Time]"
    echo "NTP=1.${MY_TLD} 2.${MY_TLD} 3.${MY_TLD} 4.${MY_TLD} time.cloudflare.com time.google.com"
  } > "$TIMESYNCD_CONF"

  systemctl restart systemd-timesyncd || true
  {
    echo "=== $(ns_ts) timedatectl timesync-status ==="
    timedatectl timesync-status 2>&1 || timedatectl status 2>&1 || true
  } >> "$LOG_FILE"

  # Telemetry timer
  if command -v systemctl >/dev/null 2>&1; then
    mkdir -p /etc/systemd/system
    cat >/etc/systemd/system/time-sync-telemetry.service <<EOF
[Unit]
Description=Time Sync Telemetry

[Service]
Type=oneshot
ExecStart=/bin/bash -c '(date; timedatectl timesync-status || timedatectl status) >> $LOG_FILE'
EOF
    cat >/etc/systemd/system/time-sync-telemetry.timer <<EOF
[Unit]
Description=Run Time Sync Telemetry every 15 minutes

[Timer]
OnBootSec=2m
OnUnitActiveSec=15m
Unit=time-sync-telemetry.service

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload || true
    systemctl enable --now time-sync-telemetry.timer || true
  fi

  log "[OK] systemd-timesyncd configured. Pool=$MY_TLD"
  exit 0
fi

log "[WARN] No chrony/ntp/ntpsec/timesyncd found. Install 'chrony' (recommended) or rerun with --install-ntpsec."
exit 3
