#!/system/bin/sh
# Android side of the camera bridge. Run as root on the phone (adb shell -> su):
#   sh /data/s20pilot/s20cam.sh list
#   sh /data/s20pilot/s20cam.sh auto [flip=1] [dry]     road camera (0) -> /data/s20pilot/share/road.ring at 20 fps
# The camera service wants the shell package, so app_process runs as the shell user (uid 2000).
JAR=/data/s20pilot/s20cam.jar
SRC=/data/s20pilot/rootfs/root/openpilot/s20/android/s20cam.jar
mkdir -p /data/s20pilot/share && chmod 777 /data/s20pilot/share
[ -f $SRC ] && cp -f $SRC $JAR
chmod 755 /data/s20pilot; chmod 644 $JAR
# the sensor service stops sending to idle (background) uids after a few seconds: mark the shell package active
cmd sensorservice set-uid-state com.android.shell active 2>/dev/null || echo "[s20cam.sh] WARN sensorservice set-uid-state failed"
exec su 2000 -c "CLASSPATH=$JAR app_process / s20cam.Main $*"
