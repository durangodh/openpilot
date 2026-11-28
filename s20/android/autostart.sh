#!/system/bin/sh
# openpilot starts by itself when the phone boots, like a comma 3: boot logo -> spinner -> offroad home screen -> driving.
# Run as root on the phone. From the PC:
#   adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/autostart.sh install"   once: autostart, boot logo, home app
#   adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/autostart.sh status"    is it running? last log lines
#   adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/autostart.sh restart"   after git pull
#   adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/autostart.sh mode demo" test the screen without a panda
#   adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/autostart.sh mode manager"  normal (default)
#   adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/autostart.sh stop"      stop until the next boot
#   adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/autostart.sh uninstall" back to a normal phone
#
# At boot (Magisk /data/adb/service.d) "start" runs: GPU/CPU at full speed, camera bridge (restarted if it dies),
# openpilot in the chroot (s20/launch_s20.sh, restarted if it dies) and the s20 openpilot app (the home screen).
# Logs: /data/s20pilot/share/log/{autostart,s20cam,openpilot}.log

ROOTFS=/data/s20pilot/rootfs
OP=$ROOTFS/root/openpilot
S20=$OP/s20
SELF=$S20/android/autostart.sh
RUN=/data/s20pilot/run
LOG=/data/s20pilot/share/log
MODE_FILE=/data/s20pilot/autostart.mode
SVC=/data/adb/service.d/s20pilot.sh
MOD=/data/adb/modules/s20bootanim
PKG=ai.s20.ui
mkdir -p $RUN $LOG /data/s20pilot/share
chmod 777 /data/s20pilot/share; chmod 755 $LOG

say() { echo "[autostart $(date +%T)] $*"; }

rotate() {  # rotate <file>: keep one old copy above 2 MB
  [ -f "$1" ] && [ "$(stat -c %s "$1" 2>/dev/null || echo 0)" -gt 2000000 ] && mv -f "$1" "$1.1"
}

alive() { [ -f "$RUN/$1.pid" ] && kill -0 "$(cat $RUN/$1.pid)" 2>/dev/null; }

mode() { cat $MODE_FILE 2>/dev/null || echo manager; }

# ---- loops (each runs in its own session, started by "start") ----

camloop() {
  while true; do
    rotate $LOG/s20cam.log
    say "camera bridge start" >> $LOG/s20cam.log
    sh $S20/android/s20cam.sh auto ctrl=off >> $LOG/s20cam.log 2>&1
    say "camera bridge exited ($?), restarting in 3 s" >> $LOG/s20cam.log
    sleep 3
  done
}

pilotloop() {
  child=""
  trap 'stop_child; exit 0' TERM INT
  while true; do
    rotate $LOG/openpilot.log
    m=$(mode)
    say "openpilot start (mode $m)" >> $LOG/openpilot.log
    # own session/process group, so stopping it also stops everything openpilot started in the chroot
    setsid sh $S20/chroot/enter_chroot.sh "bash /root/openpilot/s20/launch_s20.sh $m" >> $LOG/openpilot.log 2>&1 &
    child=$!
    echo $child > $RUN/openpilot.pid
    while kill -0 $child 2>/dev/null; do sleep 2; done
    say "openpilot exited, restarting in 5 s" >> $LOG/openpilot.log
    sleep 5
  done
}

stop_child() {
  [ -f $RUN/openpilot.pid ] || return
  p=$(cat $RUN/openpilot.pid)
  kill -TERM -$p 2>/dev/null; kill -TERM $p 2>/dev/null
  for i in 1 2 3 4 5 6 7 8 9 10; do kill -0 $p 2>/dev/null || break; sleep 1; done
  kill -KILL -$p 2>/dev/null
  rm -f $RUN/openpilot.pid
}

# ---- commands ----

start() {
  if alive pilotloop; then say "already running (autostart.sh restart to restart)"; return; fi
  say "start, mode $(mode)"
  sh $S20/android/perf.sh > /dev/null 2>&1 && say "GPU/CPU at full speed"
  setenforce 0 2>/dev/null
  settings put global stay_on_while_plugged_in 7 2>/dev/null  # screen stays on while charging (car USB)
  setsid sh $SELF camloop > /dev/null 2>&1 &
  echo $! > $RUN/camloop.pid
  setsid sh $SELF pilotloop > /dev/null 2>&1 &
  echo $! > $RUN/pilotloop.pid
  input keyevent KEYCODE_WAKEUP 2>/dev/null
  am start -n $PKG/.MainActivity > /dev/null 2>&1
  say "started: camera bridge, openpilot, screen app"
}

stop() {
  for l in pilotloop camloop; do
    alive $l && kill -TERM "$(cat $RUN/$l.pid)" 2>/dev/null
    rm -f $RUN/$l.pid
  done
  stop_child
  pkill -f s20cam.Main 2>/dev/null
  say "stopped"
}

status() {
  for l in camloop pilotloop; do alive $l && say "$l running" || say "$l NOT running"; done
  pgrep -f s20cam.Main > /dev/null && say "camera bridge (s20cam) running" || say "camera bridge NOT running"
  say "mode: $(mode)"
  if [ -f /data/s20pilot/share/ui.json ]; then
    age=$(( $(date +%s) - $(stat -c %Y /data/s20pilot/share/ui.json) ))
    say "screen data (ui.json) written ${age}s ago"
  fi
  echo "---- openpilot.log"; tail -n 15 $LOG/openpilot.log 2>/dev/null
  echo "---- s20cam.log"; tail -n 5 $LOG/s20cam.log 2>/dev/null
}

install() {
  # 1. start at boot (Magisk runs /data/adb/service.d/*.sh as root once the system is up)
  mkdir -p /data/adb/service.d
  cat > $SVC <<EOF
#!/system/bin/sh
# s20pilot: openpilot at boot (installed by s20/android/autostart.sh install; remove with autostart.sh uninstall)
until [ "\$(getprop sys.boot_completed)" = "1" ] && [ -f $SELF ]; do sleep 2; done
sleep 3
mkdir -p $LOG
sh $SELF start >> $LOG/autostart.log 2>&1
EOF
  chmod 755 $SVC
  say "autostart at boot: $SVC"

  # 2. boot logo: a Magisk module replaces the boot animation (from the next boot)
  ANIM=$S20/ui/bootanim/bootanimation.zip
  if [ -f $ANIM ]; then
    rm -rf $MOD; mkdir -p $MOD
    printf 'id=s20bootanim\nname=s20 openpilot boot logo\nversion=1\nversionCode=1\nauthor=s20pilot\ndescription=comma boot animation for openpilot on the S20 FE\n' > $MOD/module.prop
    n=0
    for d in /product/media /system/product/media /system/media; do
      if [ -f $d/bootanimation.zip ]; then
        case $d in /product/*) t=$MOD/system/product/media ;; *) t=$MOD$d ;; esac
        mkdir -p $t && cp -f $ANIM $t/bootanimation.zip && chmod 644 $t/bootanimation.zip && n=$((n+1))
      fi
    done
    [ $n = 0 ] && { mkdir -p $MOD/system/product/media; cp -f $ANIM $MOD/system/product/media/; chmod 644 $MOD/system/product/media/bootanimation.zip; }
    say "boot logo: Magisk module $MOD (from the next boot)"
  else
    say "WARN: $ANIM missing, boot logo not changed"
  fi

  # 3. the screen app, as the home screen
  cp -f $S20/ui/app/s20ui.apk /data/local/tmp/s20ui.apk && chmod 644 /data/local/tmp/s20ui.apk
  pm install -r /data/local/tmp/s20ui.apk > /dev/null && say "app installed"
  cmd role add-role-holder --user 0 android.app.role.HOME $PKG 0 2>/dev/null \
    || cmd package set-home-activity --user 0 $PKG/.MainActivity 2>/dev/null
  say "home screen: $(cmd role get-role-holders --user 0 android.app.role.HOME 2>/dev/null)"
  # no swipe lock screen (only works without a PIN/pattern): straight to the home screen like a comma 3
  locksettings set-disabled true > /dev/null 2>&1 && say "swipe lock screen off"

  [ -f $MODE_FILE ] || echo manager > $MODE_FILE
  say "installed. Reboot the phone to see it: adb reboot"
}

uninstall() {
  stop
  rm -f $SVC
  [ -d $MOD ] && touch $MOD/remove  # Magisk removes the module at the next boot
  cmd role remove-role-holder --user 0 android.app.role.HOME $PKG 0 2>/dev/null
  locksettings set-disabled false > /dev/null 2>&1
  say "uninstalled (the app stays; the boot logo is restored at the next boot; pick the home app when asked)"
}

case "$1" in
  install) install ;;
  uninstall) uninstall ;;
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 2; start ;;
  status) status ;;
  mode)
    case "$2" in manager|demo) echo $2 > $MODE_FILE; say "mode $2"; stop; sleep 2; start ;;
      *) echo "mode manager|demo (now: $(mode))" ;; esac ;;
  camloop) camloop ;;
  pilotloop) pilotloop ;;
  *) sed -n '2,13p' $0 ;;
esac
