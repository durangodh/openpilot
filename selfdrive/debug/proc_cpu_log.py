#!/usr/bin/env python3
"""Per-process CPU log for finding what loads the EON while driving.

Every INTERVAL_S it reads /proc/stat and /proc/<pid>/stat and writes one line with the total
CPU use and the busiest processes (percent of ONE core, so 100 % = a full core), e.g.

  08:41:10 total 82% | modeld 96 | plannerd 61 | controlsd 48 | remote_hud 22 | ...

to /data/media/0/cpu_log/cpu_MMDD.log, listed on http://EON_IP:7714/trace ("CPU 로그").
Run over SSH while openpilot runs (survives the SSH session ending):

  cd /data/openpilot && nohup python3 selfdrive/debug/proc_cpu_log.py >/dev/null 2>&1 &

A short spike (e.g. while the Bluetooth remote connects) disappears in a 10 s average; give
the interval in seconds as an argument to see it, e.g. every second:

  cd /data/openpilot && nohup python3 selfdrive/debug/proc_cpu_log.py 1 >/dev/null 2>&1 &

Stop it with: pkill -f proc_cpu_log.py
"""
import os
import sys
import time

LOG_DIR = "/data/media/0/cpu_log"
INTERVAL_S = 10.0
TOP_N = 12


def read_total():
  with open("/proc/stat") as f:
    parts = f.readline().split()[1:]
  values = [int(x) for x in parts]
  idle = values[3] + (values[4] if len(values) > 4 else 0)
  return sum(values), idle


def proc_name(pid):
  try:
    with open("/proc/%s/cmdline" % pid, "rb") as f:
      args = [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
  except OSError:
    args = []
  # python processes: show the module/script name, not "python"
  for a in args[1:]:
    if a.startswith("-"):
      continue
    base = os.path.basename(a)
    return base[:-3] if base.endswith(".py") else base.split(".")[-1]
  if args:
    return os.path.basename(args[0])
  try:
    with open("/proc/%s/comm" % pid) as f:
      return f.read().strip()
  except OSError:
    return str(pid)


def read_procs():
  out = {}
  for pid in os.listdir("/proc"):
    if not pid.isdigit():
      continue
    try:
      with open("/proc/%s/stat" % pid) as f:
        data = f.read()
    except OSError:
      continue
    # fields after the ")" of the comm field: utime 14, stime 15 (1-based) -> index 11, 12
    rest = data[data.rfind(")") + 2:].split()
    try:
      out[pid] = int(rest[11]) + int(rest[12])
    except (IndexError, ValueError):
      continue
  return out


def main():
  interval = INTERVAL_S
  if len(sys.argv) > 1:
    try:
      interval = min(max(float(sys.argv[1]), 0.5), 60.0)
    except ValueError:
      pass
  hz = os.sysconf("SC_CLK_TCK")
  os.makedirs(LOG_DIR, exist_ok=True)
  names = {}
  prev_total = read_total()
  prev_procs = read_procs()
  prev_time = time.monotonic()
  while True:
    time.sleep(interval)
    total = read_total()
    procs = read_procs()
    now = time.monotonic()
    dt = max(1e-3, now - prev_time)
    busy = (total[0] - prev_total[0]) - (total[1] - prev_total[1])
    total_pct = 100.0 * busy / max(1, total[0] - prev_total[0])
    usage = {}
    for pid, ticks in procs.items():
      if pid in prev_procs:
        pct = 100.0 * (ticks - prev_procs[pid]) / hz / dt
        if pct >= 0.5:
          if pid not in names:
            names[pid] = proc_name(pid)
          usage[names[pid]] = usage.get(names[pid], 0.0) + pct
    top = sorted(usage.items(), key=lambda kv: -kv[1])[:TOP_N]
    line = "%s total %.0f%% | %s" % (time.strftime("%H:%M:%S"), total_pct,
                                     " | ".join("%s %.0f" % kv for kv in top))
    try:
      with open(os.path.join(LOG_DIR, "cpu_%s.log" % time.strftime("%m%d")), "a") as f:
        f.write(line + "\n")
    except OSError:
      pass
    if sys.stdout.isatty():
      print(line, flush=True)
    names = {pid: n for pid, n in names.items() if pid in procs}
    prev_total, prev_procs, prev_time = total, procs, now


if __name__ == "__main__":
  main()
