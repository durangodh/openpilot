#!/usr/bin/env python3
"""Find which LKAS11 CF_Lkas_LdwsSysState value the stock HUD/cluster shows as which wheel icon.

controlsd sends the value written to /dev/shm/lkas_sys_state_test instead of its own,
only while openpilot is NOT engaged (carcontroller rereads it every 0.5 s).
Run over SSH with the car on and parked, watch the HUD, note the numbers:
  python3 selfdrive/debug/lkas_hud_state_test.py            # 0..15, 4 s each
  python3 selfdrive/debug/lkas_hud_state_test.py 7          # hold 7 until Ctrl+C
Ctrl+C removes the file and the normal display comes back.
"""
import argparse
import os
import time

# same path as selfdrive/car/hyundai/carcontroller.py LKAS_STATE_TEST_FILE
LKAS_STATE_TEST_FILE = "/dev/shm/lkas_sys_state_test"


def write(value):
  tmp = LKAS_STATE_TEST_FILE + ".tmp"
  with open(tmp, "w") as f:
    f.write(str(value))
  os.rename(tmp, LKAS_STATE_TEST_FILE)


def main():
  parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
  parser.add_argument("value", type=int, nargs="?", help="hold one value (0-15) instead of cycling")
  parser.add_argument("--seconds", type=float, default=4.0, help="time per value when cycling")
  args = parser.parse_args()

  try:
    if args.value is not None:
      write(max(0, min(15, args.value)))
      print(f"LKAS state {args.value} (Ctrl+C to stop)")
      while True:
        time.sleep(1)
    while True:
      for value in range(16):
        write(value)
        print(f"LKAS state {value}")
        time.sleep(args.seconds)
  except KeyboardInterrupt:
    pass
  finally:
    try:
      os.remove(LKAS_STATE_TEST_FILE)
    except OSError:
      pass
    print("\nstopped, normal HUD state restored")


if __name__ == "__main__":
  main()
