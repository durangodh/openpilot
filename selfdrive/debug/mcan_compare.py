#!/usr/bin/env python3
"""Compare M-CAN (panda bus 1, HUD side) between two setups, e.g. with and without the
MM-CAN gateway/converter, to find a message that goes missing or changes (stock HUD brightness).

Record each setup for the same conditions (same place, same light, HUD on), then diff:
  python3 selfdrive/debug/mcan_compare.py record gw      # converter / gateway in
  python3 selfdrive/debug/mcan_compare.py record stock   # switch on stock
  python3 selfdrive/debug/mcan_compare.py diff gw stock
Only frames received from the car are kept (not the ones this panda sends).
Files: /data/media/0/mcan_logs/compare_<label>.json
"""
import argparse
import json
import os
import sys
import time

LOG_DIR = "/data/media/0/mcan_logs"
MAX_VALUES = 12        # distinct values kept per byte
RATE_DIFF = 0.3        # a message rate differing by more than 30 % is reported


def path_for(label):
  return os.path.join(LOG_DIR, f"compare_{label}.json")


def summarize(frames, seconds):
  """frames: iterable of (address, bytes). -> {hex id: {rate, last, values per byte}}."""
  out = {}
  for address, data in frames:
    entry = out.setdefault(f"{address:03X}", {"count": 0, "last": "", "values": [[] for _ in range(8)]})
    entry["count"] += 1
    entry["last"] = data.hex().upper()
    for i, b in enumerate(data[:8]):
      values = entry["values"][i]
      if b not in values and len(values) < MAX_VALUES:
        values.append(b)
  for entry in out.values():
    entry["rate"] = round(entry.pop("count") / max(seconds, 1e-3), 1)
    entry["values"] = [sorted(v) for v in entry["values"]]
  return out


def diff(a, b):
  """Lines describing what differs between two summaries."""
  lines = []
  for msg in sorted(set(a) - set(b)):
    lines.append(f"0x{msg} only in A ({a[msg]['rate']} Hz)  last {a[msg]['last']}")
  for msg in sorted(set(b) - set(a)):
    lines.append(f"0x{msg} only in B ({b[msg]['rate']} Hz)  last {b[msg]['last']}")
  for msg in sorted(set(a) & set(b)):
    ra, rb = a[msg]["rate"], b[msg]["rate"]
    if max(ra, rb) > 0 and abs(ra - rb) / max(ra, rb) > RATE_DIFF:
      lines.append(f"0x{msg} rate {ra} Hz vs {rb} Hz")
    for i, (va, vb) in enumerate(zip(a[msg]["values"], b[msg]["values"])):
      if va != vb:
        fa = " ".join(f"{v:02X}" for v in va)
        fb = " ".join(f"{v:02X}" for v in vb)
        lines.append(f"0x{msg} byte {i}: A [{fa}]  B [{fb}]")
  return lines


def record(label, seconds, bus):
  import cereal.messaging as messaging
  sock = messaging.sub_sock("can", conflate=False, timeout=100)
  frames = []
  end = time.monotonic() + seconds
  print(f"recording bus {bus} for {seconds} s ...")
  while time.monotonic() < end:
    for msg in messaging.drain_sock(sock, wait_for_one=True):
      for c in msg.can:
        if c.src == bus:
          frames.append((c.address, bytes(c.dat)))
  summary = summarize(frames, seconds)
  os.makedirs(LOG_DIR, exist_ok=True)
  with open(path_for(label), "w") as f:
    json.dump({"label": label, "bus": bus, "seconds": seconds, "time": time.time(), "messages": summary}, f)
  print(f"{len(frames)} frames, {len(summary)} messages -> {path_for(label)}")
  if not frames:
    print("no frames: is the main panda's bus 1 on the M-CAN in this setup?")


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  sub = parser.add_subparsers(dest="cmd", required=True)
  r = sub.add_parser("record")
  r.add_argument("label")
  r.add_argument("--seconds", type=float, default=20.0)
  r.add_argument("--bus", type=int, default=1)
  d = sub.add_parser("diff")
  d.add_argument("a")
  d.add_argument("b")
  args = parser.parse_args()
  if args.cmd == "record":
    record(args.label, args.seconds, args.bus)
    return 0
  try:
    with open(path_for(args.a)) as f:
      a = json.load(f)["messages"]
    with open(path_for(args.b)) as f:
      b = json.load(f)["messages"]
  except (OSError, ValueError, KeyError) as e:
    print(f"cannot read: {e}")
    return 1
  lines = diff(a, b)
  print(f"A = {args.a} ({len(a)} messages), B = {args.b} ({len(b)} messages)")
  print("\n".join(lines) if lines else "no difference")
  return 0


if __name__ == "__main__":
  sys.exit(main())
