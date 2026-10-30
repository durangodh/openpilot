#!/usr/bin/env python3
"""Record raw CAN frames of one or more panda buses (default: bus 1, the DH MM-CAN tap).

loggerd is disabled on this branch, so this is the light-weight way to get
M-CAN logs for reverse engineering (e.g. the head unit's HUD turn-by-turn
messages). Frames are written in candump -l format, which SavvyCAN and
python-can read:
  (1700000000.123456) can1 4F1#0102030405060708

Run over SSH while openpilot is running (boardd publishes the frames):
  python3 selfdrive/debug/mcan_logger.py
The files can be downloaded from http://<EON IP>:7714/trace.
Press Enter to drop a numbered mark (e.g. "turn icon changed") into a
separate *_marks.log file, Ctrl+C to stop.
"""
import argparse
import os
import sys
import threading
import time

import cereal.messaging as messaging

LOG_DIR = "/data/media/0/mcan_logs"


def mark_reader(marks_f):
  n = 0
  for line in sys.stdin:
    n += 1
    # Korean typed in Termux can arrive as broken surrogates; never let a
    # label kill the mark thread, the time stamp is what matters.
    label = line.strip().encode("utf-8", "replace").decode("utf-8", "replace")
    try:
      marks_f.write(f"{time.time():.6f} MARK {n} {label}\n")
      marks_f.flush()
    except Exception as e:
      print(f"mark {n} write failed: {e}")
      continue
    print(f"mark {n}")


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--bus", type=int, nargs="+", default=[1], help="panda buses to record (default 1)")
  parser.add_argument("--dir", default=LOG_DIR, help=f"output directory (default {LOG_DIR})")
  args = parser.parse_args()

  os.makedirs(args.dir, exist_ok=True)
  name = time.strftime("mcan_%Y%m%d_%H%M%S")
  log_fn = os.path.join(args.dir, name + ".log")
  marks_fn = os.path.join(args.dir, name + "_marks.log")
  buses = set(args.bus)

  can_sock = messaging.sub_sock("can", conflate=False)
  count = 0
  ids = set()
  with open(log_fn, "w") as log_f, open(marks_fn, "w", encoding="utf-8", errors="replace") as marks_f:
    if sys.stdin.isatty():
      threading.Thread(target=mark_reader, args=(marks_f,), daemon=True).start()
    print(f"recording bus {sorted(buses)} to {log_fn}")
    print("Enter = mark, Ctrl+C = stop")

    # logMonoTime -> wall clock, so marks and video can be lined up with frames
    wall0, mono0 = None, None
    last_status = time.monotonic()
    try:
      while True:
        for evt in messaging.drain_sock(can_sock, wait_for_one=True):
          if wall0 is None:
            wall0, mono0 = time.time(), evt.logMonoTime
          t = wall0 + (evt.logMonoTime - mono0) * 1e-9
          for c in evt.can:
            if c.src in buses:
              log_f.write(f"({t:.6f}) can{c.src} {c.address:03X}#{c.dat.hex().upper()}\n")
              count += 1
              ids.add((c.src, c.address))

        now = time.monotonic()
        if now - last_status > 5.:
          log_f.flush()
          print(f"{count} frames, {len(ids)} ids")
          last_status = now
    except KeyboardInterrupt:
      pass

  print(f"\nsaved {count} frames ({len(ids)} ids) to {log_fn}")
  print(f"marks in {marks_fn}")


if __name__ == "__main__":
  main()
