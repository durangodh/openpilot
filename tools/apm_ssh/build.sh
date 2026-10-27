#!/usr/bin/env bash
# Rebuild the APM APK with the "EON / SSH" launcher icon from res/ (run by .github/workflows/build-apm-ssh.yml).
#
#   build.sh <original.apk> <apktool.jar> <build-tools dir> <keystore.p12> <output.apk>
#   env: KEYSTORE_PASSWORD, KEY_ALIAS, KEY_PASSWORD
#
# Only the launcher icon changes; code, package name and app label stay as in the original.
# The output is signed with our key, so a device must uninstall the original APM first.
set -euo pipefail

original=$1
apktool=$2
build_tools=$3
keystore=$4
output=$5
here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

java -jar "$apktool" d -f -o "$work/apm" "$original"

# Legacy and adaptive-layer PNGs for every density the APK already has.
for density in mdpi hdpi xhdpi xxhdpi xxxhdpi; do
  found=0
  for dir in "$work"/apm/res/mipmap-"$density" "$work"/apm/res/mipmap-"$density"-v*; do
    [ -d "$dir" ] || continue
    cp "$here"/res/mipmap-"$density"/*.png "$dir"/
    found=1
  done
  if [ "$found" = 0 ]; then
    mkdir -p "$work/apm/res/mipmap-$density"
    cp "$here"/res/mipmap-"$density"/*.png "$work/apm/res/mipmap-$density/"
  fi
done

# Android 8+ launchers use the adaptive icon: point both layers at our PNGs
# (the original foreground is a vector drawable we do not reuse).
for dir in "$work"/apm/res/mipmap-anydpi-v26 "$work"/apm/res/mipmap-anydpi; do
  [ -d "$dir" ] || continue
  for name in ic_launcher ic_launcher_round; do
    [ -f "$dir/$name.xml" ] || continue
    cat > "$dir/$name.xml" <<'EOF'
<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@mipmap/ic_launcher_background" />
    <foreground android:drawable="@mipmap/ic_launcher_foreground" />
</adaptive-icon>
EOF
  done
done

grep -n 'android:icon\|android:roundIcon' "$work/apm/AndroidManifest.xml" || true

java -jar "$apktool" b -o "$work/unsigned.apk" "$work/apm"
"$build_tools/zipalign" -f -p 4 "$work/unsigned.apk" "$work/aligned.apk"
"$build_tools/apksigner" sign --ks "$keystore" --ks-type PKCS12 \
  --ks-pass env:KEYSTORE_PASSWORD --ks-key-alias "$KEY_ALIAS" --key-pass env:KEY_PASSWORD \
  --out "$output" "$work/aligned.apk"
"$build_tools/apksigner" verify --verbose --print-certs "$output"
