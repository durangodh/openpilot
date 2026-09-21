#!/system/bin/sh

MODDIR=${0%/*}
LOG_FILE=/data/local/tmp/nmirror_fast_start.log
DEBUG_LOG=/data/local/tmp/nmirror_vehicle_debug.log
DEBUG_PID_FILE=/data/local/tmp/nmirror_vehicle_debug.pid
PACKAGE=com.legendn.nmirror
SERVICE=com.legendn.nmirror/.MirrorService
ACTION=com.legendn.nmirror.START_WIRELESS
EXTRA=wireless_device
AA_UUID=4de17a00-52cb-11e6-bdf4-0800200c9a66
HUD_PACKAGE=ai.comma.remotehud
HUD_SERVICE=ai.comma.remotehud/.HudService
HUD_PREFS=/data/user/0/ai.comma.remotehud/shared_prefs/remote_hud_settings.xml
HUD_BOOT_EXTRA=ai.comma.remotehud.FROM_BOOT

# Keep the boot log bounded.
if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE" 2>/dev/null)" -gt 65536 ]; then
  mv -f "$LOG_FILE" "$LOG_FILE.old"
fi
exec >>"$LOG_FILE" 2>&1

log() {
  echo "[$(date '+%F %T')] $*"
}

start_persistent_debug_log() {
  local old_pid old_cmd
  old_pid="$(cat "$DEBUG_PID_FILE" 2>/dev/null)"
  case "$old_pid" in
    ''|*[!0-9]*) old_pid="" ;;
  esac
  if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
    old_cmd="$(tr '\000' ' ' < "/proc/$old_pid/cmdline" 2>/dev/null)"
    case "$old_cmd" in
      *nmirror_vehicle_debug.log*)
        log "persistent vehicle debug logger already active (pid $old_pid)"
        return 0
        ;;
    esac
  fi

  echo "===== vehicle debug session $(date '+%F %T') boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null) =====" >>"$DEBUG_LOG"
  setsid logcat -b all -v threadtime -f "$DEBUG_LOG" -r 512 -n 3 \
    'nMirror2/Wireless:V' \
    'nMirror2/MirrorService:V' \
    'ActivityTaskManager:I' \
    'ActivityManager:I' \
    'WindowManager:I' \
    'nMirror2/AppsOnStart:V' \
    'nMirror2/Power:V' \
    'RemoteHudBoot:V' \
    'AndroidRuntime:V' \
    '*:S' </dev/null >/dev/null 2>&1 &
  DEBUG_PID=$!
  echo "$DEBUG_PID" >"$DEBUG_PID_FILE"
  sleep 1
  if kill -0 "$DEBUG_PID" 2>/dev/null; then
    log "persistent vehicle debug logger started (pid $DEBUG_PID, max 1536 KiB)"
  else
    log "persistent vehicle debug logger failed to start"
    rm -f "$DEBUG_PID_FILE"
  fi
}

valid_mac() {
  case "$1" in
    [0-9A-F][0-9A-F]:[0-9A-F][0-9A-F]:[0-9A-F][0-9A-F]:[0-9A-F][0-9A-F]:[0-9A-F][0-9A-F]:[0-9A-F][0-9A-F]) return 0 ;;
    *) return 1 ;;
  esac
}

user_unlocked() {
  cmd user is-user-unlocked 0 2>/dev/null | grep -q '^true$' && return 0
  [ "$(getprop sys.user.0.ce_available 2>/dev/null)" = "true" ] && return 0
  [ "$(getprop sys.boot_completed 2>/dev/null)" = "1" ]
}

find_receiver_mac() {
  local file mac
  for file in \
    /data/misc/bluedroid/bt_config.conf \
    /data/misc/bluetooth/bt_config.conf; do
    [ -r "$file" ] || continue
    mac="$(awk -v uuid="$AA_UUID" '
      /^\[[0-9A-Fa-f:][0-9A-Fa-f:]*\]$/ {
        candidate = substr($0, 2, length($0) - 2)
      }
      /^Service[[:space:]]*=/ && index(tolower($0), tolower(uuid)) {
        print candidate
        exit
      }
    ' "$file")"
    [ -n "$mac" ] && {
      echo "$mac"
      return 0
    }
  done
  return 1
}

hud_nav_package() {
  local selected
  [ -r "$HUD_PREFS" ] || return 1
  selected="$(sed -n 's/.*name="hud_nav_app" value="\([12]\)".*/\1/p' "$HUD_PREFS" | head -n 1)"
  case "$selected" in
    1) echo com.skt.tmap.ku ;;
    2) echo com.nhn.android.nmap ;;
    *) return 1 ;;
  esac
}

nav_on_mirror_display() {
  local package="$1"
  dumpsys activity activities 2>/dev/null | awk -v package="$package" '
    /^[[:space:]]*Display #[0-9]+/ {
      display = $2
      sub(/^#/, "", display)
      sub(/[^0-9].*$/, "", display)
    }
    display == "0" && /mResumedActivity:|topResumedActivity=/ && index($0, package) { found = 1 }
    END { exit found ? 0 : 1 }
  '
}

request_hud_sync() {
  am start-foreground-service --user 0 \
    -n "$HUD_SERVICE" \
    --ez "$HUD_BOOT_EXTRA" true >/dev/null 2>&1
}

mirror_service_active() {
  dumpsys activity services "$SERVICE" 2>/dev/null | grep -q 'ServiceRecord'
}

request_wireless_start() {
  WIRELESS_ATTEMPTS=$((WIRELESS_ATTEMPTS + 1))
  log "requesting nMirror wireless start (attempt $WIRELESS_ATTEMPTS) for **:**:**:**:${WIRELESS_MAC#*:*:*:*:}"
  if am start-foreground-service --user 0 \
    -a "$ACTION" \
    -n "$SERVICE" \
    --es "$EXTRA" "$WIRELESS_MAC" >/dev/null 2>&1; then
    log "wireless start request accepted; waiting for connection"
    return 0
  fi
  log "wireless start request rejected"
  return 1
}

log "nMirror Fast Start 1.7 begin"
start_persistent_debug_log

# Magisk service scripts start before Android is necessarily ready. Wait for
# credential-encrypted app preferences, Package Manager and Bluetooth only.
COUNT=0
while ! user_unlocked && [ "$COUNT" -lt 180 ]; do
  sleep 1
  COUNT=$((COUNT + 1))
done
if ! user_unlocked; then
  log "timeout waiting for user storage"
  exit 0
fi

COUNT=0
while ! pm path "$PACKAGE" >/dev/null 2>&1 && [ "$COUNT" -lt 60 ]; do
  sleep 1
  COUNT=$((COUNT + 1))
done
if ! pm path "$PACKAGE" >/dev/null 2>&1; then
  log "nMirror package not found"
  exit 0
fi

COUNT=0
while [ "$(settings get global bluetooth_on 2>/dev/null)" != "1" ] && [ "$COUNT" -lt 90 ]; do
  sleep 1
  COUNT=$((COUNT + 1))
done
if [ "$(settings get global bluetooth_on 2>/dev/null)" != "1" ]; then
  log "timeout waiting for Bluetooth"
  exit 0
fi

WIRELESS_MAC=""
if [ -r "$MODDIR/config.sh" ]; then
  . "$MODDIR/config.sh"
fi
if [ -z "$WIRELESS_MAC" ]; then
  WIRELESS_MAC="$(find_receiver_mac)"
fi
WIRELESS_MAC="$(printf '%s' "$WIRELESS_MAC" | tr '[:lower:]' '[:upper:]')"

WIRELESS_READY=1
if ! valid_mac "$WIRELESS_MAC"; then
  log "no unambiguous Android Auto receiver MAC found"
  WIRELESS_READY=0
fi

WIRELESS_ATTEMPTS=0
NEXT_WIRELESS_RETRY=0
if mirror_service_active; then
  log "MirrorService already active; monitoring connection and RemoteHUD handoff"
elif [ "$WIRELESS_READY" = "1" ]; then
  request_wireless_start
  NEXT_WIRELESS_RETRY=15
fi

# RemoteHUD's own boot retry can finish before nMirror creates its mirror
# capture display. Apps belong on display 0, not on that capture surface.
# Observe the whole window: an early foreground app is not a connected session.
# RemoteHUD remains the
# single source of truth for whether TMAP or Naver Map is selected.
HUD_READY=1
if ! pm path "$HUD_PACKAGE" >/dev/null 2>&1; then
  log "RemoteHUD not installed; wireless retry will continue without navigation handoff"
  HUD_READY=0
fi

COUNT=0
NAV_WAS_READY=0
while [ "$COUNT" -lt 180 ]; do
  NAV_READY=0
  if [ "$HUD_READY" = "1" ]; then
    HUD_NAV_PACKAGE="$(hud_nav_package)"
    if [ -n "$HUD_NAV_PACKAGE" ] && nav_on_mirror_display "$HUD_NAV_PACKAGE"; then
      NAV_READY=1
      if [ "$NAV_WAS_READY" != 1 ]; then
        log "RemoteHUD navigation is resumed on display 0; continue watching vehicle startup"
      fi
    fi
  fi

  NAV_WAS_READY="$NAV_READY"

  # nMirror stops MirrorService after its own bounded Bluetooth retries. A
  # successful am invocation only means Android accepted the request, so issue
  # another request while the receiver is still becoming ready during boot.
  if [ "$WIRELESS_READY" = "1" ] && \
    [ "$COUNT" -ge "$NEXT_WIRELESS_RETRY" ] && \
    ! mirror_service_active; then
    request_wireless_start
    NEXT_WIRELESS_RETRY=$((COUNT + 15))
  fi

  # A FROM_BOOT start asks the already-running HUD service to retry its own
  # display-aware navigation synchronization without opening its settings UI.
  if [ "$HUD_READY" = "1" ] && [ $((COUNT % 20)) -eq 0 ] && \
    { [ "$NAV_READY" != 1 ] || [ "$COUNT" -eq 0 ]; }; then
    request_hud_sync
    log "RemoteHUD navigation handoff requested"
  fi
  sleep 2
  COUNT=$((COUNT + 2))
done

log "startup retry window ended after 180 seconds (wireless attempts: $WIRELESS_ATTEMPTS)"

exit 0
