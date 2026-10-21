#!/bin/bash
# Push fresh YouTube cookies from a desktop browser to a VaultTube server.
#
# Reads cookies straight out of the browser with yt-dlp (no extension), keeps
# only youtube.com/google.com ones, and POSTs them to /api/cookies/youtube.
# The server picks them up on the next download; no restart needed.
#
#   vaulttube-cookies.sh setup       write the config file, then edit it
#   vaulttube-cookies.sh run         push cookies once (default)
#   vaulttube-cookies.sh install     push once, then every INTERVAL_HOURS via launchd (macOS)
#   vaulttube-cookies.sh uninstall   remove the launchd job
#   vaulttube-cookies.sh status      show what the server has
#
# Needs yt-dlp and curl (brew install yt-dlp).
set -euo pipefail

CONFIG="${VAULTTUBE_COOKIES_CONFIG:-$HOME/.config/vaulttube/cookies.conf}"
LABEL="com.vaulttube.cookies"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/vaulttube-cookies.log"
# launchd jobs get a bare PATH; add Homebrew's.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

die() { echo "error: $*" >&2; exit 1; }
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

load_config() {
    [ -f "$CONFIG" ] || die "no config at $CONFIG; run '$0 setup' first"
    # shellcheck source=/dev/null
    . "$CONFIG"
    [ -n "${VAULTTUBE_URL:-}" ] || die "VAULTTUBE_URL is not set in $CONFIG"
    VAULTTUBE_URL="${VAULTTUBE_URL%/}"
    set_auth
    BROWSER="${BROWSER:-chrome}"
    INTERVAL_HOURS="${INTERVAL_HOURS:-1}"
}

AUTH=()
set_auth() {
    if [ -n "${VAULTTUBE_COOKIE_TOKEN:-}" ]; then
        AUTH=(-H "Authorization: Bearer $VAULTTUBE_COOKIE_TOKEN")
    fi
}

cmd_setup() {
    if [ -f "$CONFIG" ]; then
        echo "Config already exists: $CONFIG"
        return
    fi
    mkdir -p "$(dirname "$CONFIG")"
    cat > "$CONFIG" <<'EOF'
# VaultTube server, as reachable from this computer.
VAULTTUBE_URL="http://vaulttube.local:5000"

# Browser (and optional profile) that is signed in to YouTube, in yt-dlp's
# --cookies-from-browser syntax: chrome, firefox, brave, edge, safari, or
# e.g. "chrome:Profile 2". A profile used ONLY to stay signed in to YouTube
# works best: browsing YouTube in it rotates the session and invalidates the
# copy on the server until the next push.
BROWSER="chrome"

# Only if the server sets VAULTTUBE_COOKIE_TOKEN.
VAULTTUBE_COOKIE_TOKEN=""

# How often the launchd job pushes (install). YouTube rotates the session
# often while the browser is in use, so hourly keeps the server's copy fresh.
INTERVAL_HOURS=1
EOF
    chmod 600 "$CONFIG"
    echo "Wrote $CONFIG; edit it, then run '$0 run' once to test."
}

cmd_run() {
    load_config
    command -v yt-dlp >/dev/null || die "yt-dlp not found (brew install yt-dlp)"
    TMP_DIR="$(mktemp -d)"
    local tmp="$TMP_DIR"
    trap 'rm -rf "$TMP_DIR"' EXIT
    chmod 700 "$tmp"

    # With no URL yt-dlp exits with a usage error, but only after writing the
    # browser's cookies to --cookies, so judge by the file, not the status.
    yt-dlp --quiet --no-warnings --cookies-from-browser "$BROWSER" \
        --cookies "$tmp/all.txt" >"$tmp/yt-dlp.log" 2>&1 || true
    if [ ! -s "$tmp/all.txt" ]; then
        cat "$tmp/yt-dlp.log" >&2
        die "yt-dlp could not read cookies from '$BROWSER'"
    fi

    # Never send other sites' cookies anywhere.
    {
        echo "# Netscape HTTP Cookie File"
        awk -F '\t' 'NF == 7 {
            d = $1; sub(/^#HttpOnly_/, "", d); sub(/^\./, "", d)
            if (d ~ /(^|\.)(youtube|google)\.com$/) print
        }' "$tmp/all.txt"
    } > "$tmp/youtube.txt"

    local response
    if ! response="$(curl -sS --max-time 60 -X POST \
            -H 'Content-Type: text/plain' ${AUTH[@]+"${AUTH[@]}"} \
            --data-binary "@$tmp/youtube.txt" \
            -w '\n%{http_code}' "$VAULTTUBE_URL/api/cookies/youtube")"; then
        die "could not reach $VAULTTUBE_URL"
    fi
    local code="${response##*$'\n'}"
    local body="${response%$'\n'*}"
    if [ "$code" != "200" ]; then
        die "server rejected the cookies (HTTP $code): $body"
    fi
    log "pushed YouTube cookies from $BROWSER to $VAULTTUBE_URL: $body"
}

cmd_status() {
    load_config
    curl -sS --max-time 30 ${AUTH[@]+"${AUTH[@]}"} "$VAULTTUBE_URL/api/cookies/youtube"
    echo
}

# Fixed clock times (hourly on the hour, or every INTERVAL_HOURS from 08:00)
# rather than StartInterval: launchd runs a calendar job missed during sleep
# on wake, while an interval timer only advances while the Mac is awake.
calendar_entries() {
    local h
    if [ "$INTERVAL_HOURS" -le 1 ]; then
        echo "        <dict><key>Minute</key><integer>0</integer></dict>"
        return
    fi
    for ((h = 8; h < 32; h += INTERVAL_HOURS)); do
        echo "        <dict><key>Hour</key><integer>$((h % 24))</integer><key>Minute</key><integer>0</integer></dict>"
    done
}

cmd_install() {
    [ "$(uname)" = "Darwin" ] || die "install uses launchd (macOS); use cron elsewhere"
    load_config
    # Run once in the foreground so any Keychain prompt ("security wants to
    # use Chrome Safe Storage") can be answered with Always Allow.
    cmd_run
    local script
    script="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
    mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$script</string>
        <string>run</string>
    </array>
    <key>StartCalendarInterval</key>
    <array>
$(calendar_entries)
    </array>
    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>$LOG</string>
    <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "Installed: pushes every $INTERVAL_HOURS h (missed runs catch up on wake). Log: $LOG"
}

cmd_uninstall() {
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed $LABEL"
}

case "${1:-run}" in
    setup) cmd_setup ;;
    run) cmd_run ;;
    status) cmd_status ;;
    install) cmd_install ;;
    uninstall) cmd_uninstall ;;
    *) die "usage: $0 [setup|run|status|install|uninstall]" ;;
esac
