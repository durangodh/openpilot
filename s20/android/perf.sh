#!/system/bin/sh
# Pin the Adreno 650 and the CPUs to full speed (comma devices do the same while driving). Run as root:
#   adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/perf.sh"        # performance
#   adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/perf.sh show"   # just print the state
K=/sys/class/kgsl/kgsl-3d0
show() {
  echo "GPU governor: $(cat $K/devfreq/governor 2>/dev/null)  cur: $(cat $K/devfreq/cur_freq 2>/dev/null)  max: $(cat $K/devfreq/max_freq 2>/dev/null)"
  echo "GPU busy: $(cat $K/gpu_busy_percentage 2>/dev/null)  pwrlevel: $(cat $K/default_pwrlevel 2>/dev/null) (0 = fastest)"
  for p in /sys/devices/system/cpu/cpufreq/policy*; do
    echo "CPU $(basename $p): $(cat $p/scaling_governor)  cur $(cat $p/scaling_cur_freq)  max $(cat $p/cpuinfo_max_freq)"
  done
  echo "temps: $(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | head -8 | tr '\n' ' ')"
}
if [ "$1" != "show" ]; then
  echo performance > $K/devfreq/governor 2>/dev/null
  cat $K/devfreq/max_freq > $K/devfreq/min_freq 2>/dev/null
  echo 0 > $K/default_pwrlevel 2>/dev/null; echo 0 > $K/min_pwrlevel 2>/dev/null; echo 0 > $K/thermal_pwrlevel 2>/dev/null
  echo 1 > $K/force_clk_on 2>/dev/null; echo 1 > $K/force_bus_on 2>/dev/null; echo 1 > $K/force_rail_on 2>/dev/null
  echo 1000000 > $K/idle_timer 2>/dev/null
  for p in /sys/devices/system/cpu/cpufreq/policy*; do
    echo performance > $p/scaling_governor 2>/dev/null
    cat $p/cpuinfo_max_freq > $p/scaling_min_freq 2>/dev/null
  done
  echo "set GPU and CPUs to performance"
fi
show
