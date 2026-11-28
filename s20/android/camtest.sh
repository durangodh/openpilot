#!/system/bin/sh
# One-command camera test. From the PC:
#   adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/camtest.sh"
# Tries each camera setting for 15 s, prints frame rate / brightness / what the camera really applied,
# and saves a picture per setting to the phone's Download folder (open it in the Gallery).
REPO=/data/s20pilot/rootfs/root/openpilot
CAM="sh $REPO/s20/android/s20cam.sh"
LOG=/data/s20pilot/share/cam.log
OUT=/sdcard/Download
pkill -f s20cam.Main 2>/dev/null

n=0
for opts in "ctrl=off" "ctrl=off tmpl=manual" "ctrl=off aerange=7-30" "ctrl=off tmpl=manual aerange=7-30"; do
  n=$((n+1))
  echo "=================== $n: $opts"
  $CAM auto $opts > $LOG 2>&1 &
  sleep 15
  grep -E "fps|FAIL" $LOG | tail -2 | sed 's/\[s20cam\] //'
  sh $REPO/s20/chroot/enter_chroot.sh "cd /root/openpilot && python3 s20/camerad/ring.py /s20share/road.ring 3" 2>&1 | grep -E "fps read|no frame|Error" 
  cp /data/s20pilot/share/road.png $OUT/s20cam_$n.png 2>/dev/null && echo "picture: Download/s20cam_$n.png"
  pkill -f s20cam.Main
  sleep 2
done
echo "=================== done"
