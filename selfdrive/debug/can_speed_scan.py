#!/usr/bin/env python3
"""Find the bitrate of an unknown CAN bus (e.g. Genesis DH MM-CAN) on a panda bus.

The panda is put in SAFETY_SILENT, so it never ACKs or sends error frames,
then each candidate speed is tried and the received frames are counted.
The right speed shows many frames with a stable set of IDs; a wrong speed
shows (almost) nothing.

Stop openpilot first (it owns the panda), then run with --wait and connect
the unknown bus only once the panda is SILENT:
  tmux kill-session -t comma
  python3 selfdrive/debug/can_speed_scan.py --wait
Afterwards reboot the device so boardd sets the panda up again.
"""
import argparse
import threading
import time
from collections import Counter

from panda import Panda

DEFAULT_SPEEDS = [500, 250, 125, 100, 83.3, 50, 33.3]


def scan(p, bus, speed, seconds):
  p.set_can_speed_kbps(bus, speed)
  time.sleep(0.2)
  p.can_clear(0xFFFF)

  counts = Counter()
  ref_cnt = 0
  start = time.monotonic()
  last_hb = 0.
  while time.monotonic() - start < seconds:
    now = time.monotonic()
    # without heartbeats the panda falls back to NOOUTPUT, which ACKs on every bus
    if now - last_hb > 0.5:
      p.send_heartbeat(engaged=False)
      last_hb = now
    for addr, _, _, src in p.can_recv():
      if src == bus:
        counts[addr] += 1
      elif src == 0:
        ref_cnt += 1
    time.sleep(0.01)
  return counts, ref_cnt


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--bus", type=int, default=1, help="panda bus to scan (default 1)")
  parser.add_argument("--speeds", type=float, nargs="+", default=DEFAULT_SPEEDS, help="candidate speeds in kbps")
  parser.add_argument("--wait", action="store_true", help="wait for Enter before scanning (connect wires while SILENT)")
  parser.add_argument("--restore", type=float, default=100, help="speed to leave the bus at (firmware default 100)")
  parser.add_argument("--seconds", type=float, default=3., help="listen time per speed")
  args = parser.parse_args()

  p = Panda()
  p.set_safety_mode(Panda.SAFETY_SILENT)
  p.send_heartbeat(engaged=False)
  print("panda in SILENT mode on all buses")
  if args.wait:
    # the panda boots live at 500 kbps, so only connect the unknown bus once it is SILENT
    # (send heartbeats while waiting, or the panda drops back to live NOOUTPUT)
    stop = threading.Event()

    def heartbeat():
      while not stop.wait(0.5):
        p.send_heartbeat(engaged=False)

    hb = threading.Thread(target=heartbeat, daemon=True)
    hb.start()
    input("connect the bus wires now, then press Enter to start scanning...")
    stop.set()
    hb.join()
  print(f"scanning bus {args.bus}\n")
  print(f"{'kbps':>6} {'frames':>7} {'ids':>5} {'bus0 ref':>9}  top ids")

  results = []
  try:
    for speed in args.speeds:
      counts, ref_cnt = scan(p, args.bus, speed, args.seconds)
      total = sum(counts.values())
      top = ", ".join(f"{a}({c})" for a, c in counts.most_common(5))
      print(f"{speed:>6g} {total:>7} {len(counts):>5} {ref_cnt:>9}  {top}")
      results.append((total, len(counts), speed))
  finally:
    # stay SILENT and go back to the firmware default (MCAN_SPEED, 100 kbps on bus 1)
    p.set_can_speed_kbps(args.bus, args.restore)
    p.send_heartbeat(engaged=False)

  best = max(results)
  print()
  if best[0] == 0:
    print("no frames at any speed: check wiring (High/Low, panda bus) and that the bus is awake (ACC/IG on)")
  else:
    print(f"best: {best[2]:g} kbps ({best[0]} frames, {best[1]} ids)")
  print("bus0 ref should be large at every step; if it is 0 the panda is not receiving at all")
  print("reboot the device before driving")


if __name__ == "__main__":
  main()
