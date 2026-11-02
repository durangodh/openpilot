#!/usr/bin/env python3
"""Bench check: does the host log LKAS11 frames the panda rejects?

Car on, PARKED, openpilot NOT engaged. While this runs, carcontroller sends LKAS11
with torque 10 and the steering request bit off (selfdrive/car/hyundai/carcontroller.py
LKAS11_REJECT_TEST_FILE). The panda only accepts a step of 3 from its last torque (0),
so every such frame is rejected and never reaches the MDPS; it is ignored while
engaged, moving or requesting. Afterwards the MDPS log (http://EON_IP:7714/trace,
"MDPS 로그") must show "LKAS11 REJECTED | torque=10 ..." and "LKAS11 rejection end".
  python3 selfdrive/debug/lkas11_reject_test.py            # 3 s
  python3 selfdrive/debug/lkas11_reject_test.py --seconds 5
"""
import argparse
import os
import time

# same path as selfdrive/car/hyundai/carcontroller.py LKAS11_REJECT_TEST_FILE
LKAS11_REJECT_TEST_FILE = "/dev/shm/lkas11_reject_test"


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("--seconds", type=float, default=3.0)
  args = parser.parse_args()
  try:
    open(LKAS11_REJECT_TEST_FILE, "w").close()
    print(f"sending rejected LKAS11 for {args.seconds:.0f} s (parked, not engaged)...")
    time.sleep(max(1.0, args.seconds) + 0.5)   # carcontroller rereads the flag every 0.5 s
  finally:
    try:
      os.remove(LKAS11_REJECT_TEST_FILE)
    except OSError:
      pass
  print("done. Check the MDPS log for 'LKAS11 REJECTED | torque=10' and 'LKAS11 rejection end'.")


if __name__ == "__main__":
  main()
