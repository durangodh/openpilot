#!/system/bin/sh
# S20 FE: Ubuntu 24.04 arm64 chroot one-time setup.
# Run as root on the phone (adb shell -> su).
#
# Before running, push the Ubuntu base tarball from the PC:
#   adb push ubuntu-base-24.04.*-base-arm64.tar.gz /data/local/tmp/ubuntu-base.tar.gz
# (download: https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/)

set -e

ROOT=/data/s20pilot/rootfs
TARBALL=/data/local/tmp/ubuntu-base.tar.gz

if [ "$(id -u)" != "0" ]; then
  echo "run as root (su)"; exit 1
fi
if [ ! -f "$TARBALL" ]; then
  echo "missing $TARBALL (adb push it first)"; exit 1
fi
if [ -d "$ROOT/usr" ]; then
  echo "$ROOT already exists, skipping extract"
else
  mkdir -p "$ROOT"
  echo "extracting..."
  tar -xzf "$TARBALL" -C "$ROOT"
fi

# DNS and hosts for apt inside the chroot
echo "nameserver 8.8.8.8" > "$ROOT/etc/resolv.conf"
echo "nameserver 1.1.1.1" >> "$ROOT/etc/resolv.conf"
echo "127.0.0.1 localhost" > "$ROOT/etc/hosts"

# Android's network permission groups: without these, apt cannot open sockets
# when Android's paranoid networking is on.
grep -q "^aid_inet:" "$ROOT/etc/group" || cat >> "$ROOT/etc/group" <<'EOF'
aid_inet:x:3003:root,_apt
aid_net_raw:x:3004:root,_apt
EOF
usermod_line='_apt:x:42:65534::/nonexistent:/usr/sbin/nologin'
grep -q "^_apt:" "$ROOT/etc/passwd" || echo "$usermod_line" >> "$ROOT/etc/passwd"

cp "$(dirname "$0")/enter_chroot.sh" /data/s20pilot/enter_chroot.sh
chmod 755 /data/s20pilot/enter_chroot.sh

echo "done. next:"
echo "  sh /data/s20pilot/enter_chroot.sh"
echo "  (inside) apt update && apt install -y git python3 python3-pip sudo usbutils"
