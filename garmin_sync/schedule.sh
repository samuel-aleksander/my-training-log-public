#!/usr/bin/env bash
# Run garmin_sync.py at 12:30 and 21:30 with launchd (a missed time runs on wake).
#   ./schedule.sh install    create or update the job (picks up MARATHON_INBOX_DIR if set)
#   ./schedule.sh remove     delete it
#   ./schedule.sh run        run it now, as launchd would
#   ./schedule.sh log        show the end of its log
set -euo pipefail

cd "$(dirname "$0")"
here="$(pwd)"
label="com.marathon.garmin-sync"
plist="$HOME/Library/LaunchAgents/$label.plist"
logfile="$HOME/Library/Logs/marathon-garmin-sync.log"
domain="gui/$(id -u)"

case "${1:-}" in
  install)
    if [ ! -x .venv/bin/python ]; then
      echo "Create the virtualenv first (see README.md)." >&2
      exit 1
    fi
    env_block=""
    if [ -n "${MARATHON_INBOX_DIR:-}" ]; then
      env_block="  <key>EnvironmentVariables</key>
  <dict><key>MARATHON_INBOX_DIR</key><string>$MARATHON_INBOX_DIR</string></dict>"
    fi
    mkdir -p "$(dirname "$plist")"
    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>$here/.venv/bin/python</string>
    <string>-I</string>
    <string>$here/garmin_sync.py</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>30</integer></dict>
    <dict><key>Hour</key><integer>21</integer><key>Minute</key><integer>30</integer></dict>
  </array>
$env_block
  <key>StandardOutPath</key><string>$logfile</string>
  <key>StandardErrorPath</key><string>$logfile</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
EOF
    plutil -lint -s "$plist"
    launchctl bootout "$domain/$label" 2>/dev/null || true
    launchctl bootstrap "$domain" "$plist"
    echo "Installed: runs at 12:30 and 21:30. Log: $logfile"
    ;;
  remove)
    launchctl bootout "$domain/$label" 2>/dev/null || true
    rm -f "$plist"
    echo "Removed."
    ;;
  run)
    launchctl kickstart "$domain/$label"
    echo "Started. See: ./schedule.sh log"
    ;;
  log)
    tail -n 30 "$logfile"
    ;;
  *)
    sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
