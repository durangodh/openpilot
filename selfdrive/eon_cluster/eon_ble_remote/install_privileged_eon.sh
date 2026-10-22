#!/system/bin/sh
# Install the R1 BLE receiver as an Android 6 privileged system app.
# Run on a rooted EON only after reviewing the system-partition change.
set -eu

package=ai.comma.eonbleremote
source_apk=/data/openpilot/apk/ai.comma.eonbleremote.apk
dest_dir=/system/priv-app/EonBleRemote
dest_apk="$dest_dir/EonBleRemote.apk"
case "${1:-}" in
  '') ;;
  probe)
    package=ai.comma.eonbleremote.probe
    source_apk=/data/openpilot/apk-probe/ai.comma.eonbleremote.probe.apk
    dest_dir=/system/priv-app/EonBleRemoteProbe
    dest_apk="$dest_dir/EonBleRemoteProbe.apk"
    ;;
  *) echo 'Usage: install_privileged_eon.sh [probe]' >&2; exit 1 ;;
esac

if [ "$(id -u)" != 0 ] || [ "$(getprop ro.build.version.sdk)" != 23 ]; then
  echo 'Android 6 EON root shell required' >&2
  exit 1
fi
if [ ! -f "$source_apk" ] || [ ! -d /system/priv-app ]; then
  echo 'EON BLE APK or /system/priv-app is missing' >&2
  exit 1
fi
if [ -e "$dest_dir" ]; then
  echo "Refusing to replace existing $dest_dir" >&2
  exit 1
fi

current_apk=$(pm path "$package" 2>/dev/null | sed -n 's/^package://p' | head -n 1)
if [ -n "$current_apk" ]; then
  backup=/data/local/tmp/eonbleremote-before-priv.apk
  cp "$current_apk" "$backup"
  echo "Previous APK saved at $backup"
fi

system_ready=0
cleanup_mount() {
  if [ "$system_ready" != 1 ]; then
    rm -f "$dest_apk"
    rmdir "$dest_dir" 2>/dev/null || true
  fi
  mount -o remount,ro /system || true
}
mount -o remount,rw /system
trap cleanup_mount EXIT
options=$(awk '$2 == "/system" {print $4}' /proc/mounts)
case ",$options," in
  *,rw,*) ;;
  *) echo 'Could not remount /system read-write' >&2; exit 1 ;;
esac
mkdir "$dest_dir"
cp "$source_apk" "$dest_apk"
chown 0:0 "$dest_dir" "$dest_apk"
chmod 755 "$dest_dir"
chmod 644 "$dest_apk"
if command -v restorecon >/dev/null 2>&1; then restorecon -R "$dest_dir"; fi
if ! cmp -s "$source_apk" "$dest_apk"; then
  echo 'APK copy verification failed' >&2
  exit 1
fi
system_ready=1
cleanup_mount
trap - EXIT
options=$(awk '$2 == "/system" {print $4}' /proc/mounts)
case ",$options," in
  *,ro,*) ;;
  *) echo 'System APK copied, but /system is still writable; stop before uninstall' >&2; exit 1 ;;
esac

if [ -n "$current_apk" ]; then
  result=$(pm uninstall "$package" 2>&1) || true
  case "$result" in
    *Success*) ;;
    *)
      mount -o remount,rw /system
      rm -f "$dest_apk"
      rmdir "$dest_dir"
      mount -o remount,ro /system
      echo "Old APK uninstall failed: $result" >&2
      exit 1
      ;;
  esac
fi

echo 'System APK installed. Reboot EON, then verify pm path and BLUETOOTH_PRIVILEGED.'
