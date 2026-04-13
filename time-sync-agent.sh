#!/usr/bin/env bash
# ============================================================
# time-sync-agent.sh
# NTPsecDispatcher Unix/macOS launcher
#
# Prefers chrony; falls back to ntpsec/ntpd, then timesyncd.
# NTS enabled only when chrony version supports it.
# Memoization: /var/cache/time-sync/config.cache
# Telemetry:   /var/log/time-sync/status.log (nanosecond ts)
# Modes: --mode=fast (default) | --mode=safe
#
# Usage:
#   sudo ./time-sync-agent.sh [POOL_OVERRIDE] [--mode=fast|safe] [--install-ntpsec]
# ============================================================

set -euo pipefail
IFS=$'\n\t'

# ---- Args ----
POOL_ARG=""
MODE="safe"
INSTALL_NTPSEC=0

for arg in "$@"; do
    case "$arg" in
        --mode=*)        MODE="${arg#*=}" ;;
        --install-ntpsec) INSTALL_NTPSEC=1 ;;
        --*)             ;;                       # ignore unknown flags
        *)               [[ -z "$POOL_ARG" ]] && POOL_ARG="$arg" ;;
    esac
done
[[ "$MODE" == "fast" || "$MODE" == "safe" ]] || MODE="safe"

# ---- Root check ----
if [[ $EUID -ne 0 ]]; then
    echo "[ERROR] Run as root: sudo $0 $*"
    exit 1
fi

# ---- Paths ----
CACHE_DIR="${TIMESYNC_CACHE:-/var/cache/time-sync}"
CACHE_FILE="$CACHE_DIR/config.cache"
LOG_DIR="/var/log/time-sync"
LOG_FILE="$LOG_DIR/status.log"
CACHE_TTL_SECONDS=604800  # 1 week

mkdir -p "$CACHE_DIR" "$LOG_DIR"

DEFAULT_POOL="pool.chrony.eu"
FALLBACKS=("time.cloudflare.com" "time.google.com" "pool.ntp.org")

# ---- Logging (fixed: was routing all output to /dev/null) ----
ns_ts() {
    # macOS date does not support %N; fall back to seconds-only
    if date -u +"%N" 2>/dev/null | grep -q '^[0-9]'; then
        date -u +"%Y-%m-%dT%H:%M:%S.%NZ" | sed 's/\([0-9]\{9\}\)[0-9]*/\1/'
    else
        date -u +"%Y-%m-%dT%H:%M:%SZ"
    fi
}

log() {
    local line="[$(ns_ts)] $*"
    echo "$line"                          # console output (was suppressed — now fixed)
    echo "$line" >> "$LOG_FILE"
}

# ---- Cache helpers (safe: no eval/source) ----
load_cache() {
    [[ -f "$CACHE_FILE" ]] || return 1
    # Parse key=value without sourcing (avoids arbitrary code execution)
    local cached_at="" my_tld=""
    while IFS='=' read -r key val; do
        [[ "$key" == "CACHED_AT" ]] && cached_at="$val"
        [[ "$key" == "MY_TLD"    ]] && my_tld="$val"
    done < "$CACHE_FILE"
    [[ -z "$cached_at" || -z "$my_tld" ]] && return 1
    local now; now=$(date +%s)
    (( now - cached_at < CACHE_TTL_SECONDS )) || return 1
    MY_TLD="$my_tld"
    return 0
}

save_cache() {
    cat > "$CACHE_FILE" <<EOF
CACHED_AT=$(date +%s)
MY_TLD=$MY_TLD
MODE=$MODE
EOF
    chmod 600 "$CACHE_FILE"
}

# ---- DNS resolution check ----
resolve_ok() {
    local host="$1"
    getent ahosts "$host" >/dev/null 2>&1 \
        || dig +short +time=3 +tries=1 "$host" >/dev/null 2>&1 \
        || host -W 3 "$host" >/dev/null 2>&1
}

# ---- Pool selection ----
MY_TLD=""
if [[ -n "$POOL_ARG" ]]; then
    MY_TLD="$POOL_ARG"
    log "Pool override: $MY_TLD"
elif load_cache; then
    log "Using cached pool: $MY_TLD (mode=$MODE)"
else
    MY_TLD="$DEFAULT_POOL"
fi

if ! resolve_ok "$MY_TLD"; then
    log "Primary pool '$MY_TLD' not resolvable; trying fallbacks..."
    FOUND=0
    for p in "${FALLBACKS[@]}"; do
        if resolve_ok "$p"; then
            MY_TLD="$p"
            log "Using fallback pool: $MY_TLD"
            FOUND=1
            break
        fi
    done
    if (( FOUND == 0 )); then
        log "[FAIL] No resolvable pools. Check DNS/network."
        exit 2
    fi
fi

# ---- Async DNS warm-up (background, non-fatal) ----
for i in 1 2 3 4; do
    { dig +short +time=2 "${i}.${MY_TLD}" >/dev/null 2>&1 || true; } &
done

save_cache

# ---- Mode parameters ----
if [[ "$MODE" == "fast" ]]; then
    MINPOLL=4; MAXPOLL=8; MAKESTEP="0.1 10"
else
    MINPOLL=6; MAXPOLL=10; MAKESTEP="1.0 3"
fi

# ---- NTS capability detection (chrony >= 4.0) ----
chrony_nts_supported() {
    chronyd --version 2>&1 | awk '/^chronyd \(chrony\)/ {
        split($3, v, "."); if (v[1]+0 >= 4) exit 0; exit 1
    }'
}

# ---- Optional ntpsec install ----
install_ntpsec() {
    command -v ntpd >/dev/null 2>&1 && return 0
    if   command -v apt-get >/dev/null 2>&1; then apt-get update -qq && apt-get install -y ntpsec
    elif command -v dnf     >/dev/null 2>&1; then dnf install -y ntpsec
    elif command -v yum     >/dev/null 2>&1; then yum install -y ntpsec
    elif command -v brew    >/dev/null 2>&1; then brew install ntpsec
    fi || true
}
if (( INSTALL_NTPSEC )); then
    log "Attempting ntpsec installation..."
    install_ntpsec
fi

# ---- Atomic config write ----
write_config() {
    local path="$1" content="$2"
    local dir; dir="$(dirname "$path")"
    mkdir -p "$dir"
    # Backup existing
    [[ -f "$path" ]] && cp -a "$path" "${path}.bak.$(date +%s)" 2>/dev/null || true
    # Atomic write
    local tmp="${path}.tmp.$$"
    printf '%s\n' "$content" > "$tmp"
    mv "$tmp" "$path"
}

# ---- Systemd timer ----
install_telemetry_timer() {
    local exec_cmd="$1"
    command -v systemctl >/dev/null 2>&1 || return 0
    mkdir -p /etc/systemd/system
    cat > /etc/systemd/system/time-sync-telemetry.service <<EOF
[Unit]
Description=Time Sync Telemetry

[Service]
Type=oneshot
ExecStart=/bin/bash -c '(date; $exec_cmd) >> $LOG_FILE'
EOF
    cat > /etc/systemd/system/time-sync-telemetry.timer <<EOF
[Unit]
Description=Time Sync Telemetry timer

[Timer]
OnBootSec=2m
OnUnitActiveSec=15m
Unit=time-sync-telemetry.service

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload   2>/dev/null || true
    systemctl enable --now time-sync-telemetry.timer 2>/dev/null || true
}

# ============================================================
# chrony (preferred)
# ============================================================
if command -v chronyd >/dev/null 2>&1; then
    CONF="/etc/chrony/chrony.conf"
    [[ -f /etc/chrony.conf ]] && CONF="/etc/chrony.conf"

    NTS_SUFFIX=""
    chrony_nts_supported && NTS_SUFFIX=" nts"

    write_config "$CONF" "# Managed by time-sync-agent.sh — do not edit by hand
driftfile /var/lib/chrony/chrony.drift
makestep $MAKESTEP
rtcsync
leapsectz right/UTC
logdir /var/log/chrony
pool 1.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL
pool 2.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL
pool 3.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL
pool 4.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL
server time.cloudflare.com iburst${NTS_SUFFIX} minpoll $MINPOLL maxpoll $MAXPOLL
server time.google.com     iburst${NTS_SUFFIX} minpoll $MINPOLL maxpoll $MAXPOLL"

    systemctl restart chronyd 2>/dev/null || service chrony restart 2>/dev/null || true

    {
        echo "=== $(ns_ts) chronyc tracking ==="
        chronyc tracking 2>&1 || true
        echo "=== $(ns_ts) chronyc sources ==="
        chronyc sources -v 2>&1 || true
    } >> "$LOG_FILE"

    install_telemetry_timer "chronyc tracking; chronyc sources -v"
    log "[OK] chrony configured — pool=$MY_TLD mode=$MODE"
    exit 0
fi

# ============================================================
# ntpsec / ntpd
# ============================================================
if command -v ntpd >/dev/null 2>&1; then
    CONF="/etc/ntp.conf"
    [[ -d /etc/ntpsec ]] && CONF="/etc/ntpsec/ntp.conf"

    write_config "$CONF" "# Managed by time-sync-agent.sh — do not edit by hand
driftfile /var/lib/ntp/drift
tinker panic 0
pool 1.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL
pool 2.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL
pool 3.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL
pool 4.${MY_TLD} iburst minpoll $MINPOLL maxpoll $MAXPOLL"

    systemctl restart ntpsec 2>/dev/null \
        || systemctl restart ntp 2>/dev/null \
        || service ntpsec restart 2>/dev/null \
        || service ntp restart 2>/dev/null \
        || true

    { echo "=== $(ns_ts) ntpq -p ==="; ntpq -p 2>&1 || true; } >> "$LOG_FILE"
    install_telemetry_timer "ntpq -p"
    log "[OK] ntpsec/ntp configured — pool=$MY_TLD mode=$MODE"
    exit 0
fi

# ============================================================
# systemd-timesyncd (last resort)
# ============================================================
if systemctl list-unit-files 2>/dev/null | grep -q '^systemd-timesyncd'; then
    NTP_LIST="1.${MY_TLD} 2.${MY_TLD} 3.${MY_TLD} 4.${MY_TLD} time.cloudflare.com time.google.com"
    write_config "/etc/systemd/timesyncd.conf" "# Managed by time-sync-agent.sh
[Time]
NTP=$NTP_LIST"

    systemctl restart systemd-timesyncd || true
    {
        echo "=== $(ns_ts) timedatectl timesync-status ==="
        timedatectl timesync-status 2>&1 || timedatectl status 2>&1 || true
    } >> "$LOG_FILE"
    install_telemetry_timer "timedatectl timesync-status || timedatectl status"
    log "[OK] systemd-timesyncd configured — pool=$MY_TLD"
    exit 0
fi

log "[WARN] No supported NTP daemon found. Install chrony: apt-get install -y chrony"
log "       Or rerun with: --install-ntpsec"
exit 3
