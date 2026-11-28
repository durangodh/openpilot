#!/system/bin/sh
# Mount the devices openpilot needs and enter the Ubuntu chroot.
# Run as root on the phone. Safe to run more than once.

ROOT=/data/s20pilot/rootfs

mnt() {  # mnt <src> <dst> [opts]
  mkdir -p "$2"
  grep -q " $2 " /proc/mounts || mount $3 "$1" "$2"
}

mnt /dev      "$ROOT/dev"     "-o bind"
# bind the host devpts: a new devpts instance would not match the bound /dev/ptmx
mnt /dev/pts  "$ROOT/dev/pts" "-o bind"
mnt proc      "$ROOT/proc"    "-t proc"
mnt sysfs     "$ROOT/sys"     "-t sysfs"
# msgq / VisionIPC shared memory (Android has no /dev/shm)
mnt tmpfs     "$ROOT/dev/shm" "-t tmpfs -o size=512m,mode=1777"
mnt tmpfs     "$ROOT/tmp"     "-t tmpfs -o mode=1777"
# shared folder with the Android side (camera bridge, GPS)
mkdir -p /data/s20pilot/share
mnt /data/s20pilot/share "$ROOT/s20share" "-o bind"

# GPU (tinygrad QCOM) and USB (panda) must be visible
[ -e "$ROOT/dev/kgsl-3d0" ] || echo "WARN: /dev/kgsl-3d0 missing"
[ -d "$ROOT/dev/bus/usb" ]  || echo "WARN: /dev/bus/usb missing (no OTG device?)"

# SELinux blocks a lot from the chroot; permissive while porting
setenforce 0 2>/dev/null

# with arguments: run that command in the chroot and return (used by test scripts); without: interactive shell
if [ $# -gt 0 ]; then
  exec chroot "$ROOT" /usr/bin/env -i \
    HOME=/root TERM="$TERM" LANG=C.UTF-8 TZ=Asia/Seoul DEBIAN_FRONTEND=noninteractive \
    PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    /bin/bash -lc "$*"
fi
exec chroot "$ROOT" /usr/bin/env -i \
  HOME=/root TERM="$TERM" LANG=C.UTF-8 TZ=Asia/Seoul DEBIAN_FRONTEND=noninteractive \
  PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  /bin/bash -l
