#!/system/bin/sh
# Android side of the camera bridge. Run as root on the phone (adb shell -> su):
#   sh /data/s20pilot/s20cam.sh list
#   sh /data/s20pilot/s20cam.sh stream 0:1920x1080:/data/s20pilot/share/road.ring 2:1920x1080:/data/s20pilot/share/wide.ring
# The camera service wants the shell package, so app_process runs as the shell user (uid 2000).
JAR=/data/s20pilot/s20cam.jar
mkdir -p /data/s20pilot/share && chmod 777 /data/s20pilot/share
cp -f /data/local/tmp/s20cam.jar $JAR 2>/dev/null; chmod 644 $JAR
exec su 2000 -c "CLASSPATH=$JAR app_process / s20cam.Main $*"
