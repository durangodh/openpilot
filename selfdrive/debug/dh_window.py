#!/usr/bin/env python3
"""DH 창문 (ESP32 B-CAN, 펌웨어 2026-11-21 이상). bcan_blinker 가 실행 중이어야 한다.

    python selfdrive/debug/dh_window.py 2 down     # 조수석 앞 끝까지 내림
    python selfdrive/debug/dh_window.py 2 up       # 조수석 앞 1.5초 올림 (반복해서 더 올림)
    python selfdrive/debug/dh_window.py all stop   # 모두 멈춤
    창문: 1 운전석 앞, 2 조수석 앞, 3 운전석 뒤, 4 조수석 뒤, all 모두
    결과: grep window /data/bcan_blinker.log | tail
"""
import json
import os
import sys
import time

from selfdrive.eon_cluster.bcan_blinker_core import WINDOW_FILE

WINDOWS = {"1": "1", "2": "2", "3": "3", "4": "4", "all": "A", "a": "A"}
DIRS = {"down": "D", "d": "D", "up": "U", "u": "U", "stop": "S", "s": "S"}


def main():
  if len(sys.argv) != 3 or sys.argv[1].lower() not in WINDOWS or sys.argv[2].lower() not in DIRS:
    print(__doc__)
    return 1
  cmd = "W" + WINDOWS[sys.argv[1].lower()] + DIRS[sys.argv[2].lower()]
  tmp = WINDOW_FILE + ".tmp"
  with open(tmp, "w") as f:
    json.dump({"cmd": cmd, "ts": time.time(), "id": f"{time.time():.3f}"}, f)
  os.replace(tmp, WINDOW_FILE)
  print(f"{cmd} 요청. 결과: grep window /data/bcan_blinker.log | tail -n 3")
  return 0


if __name__ == "__main__":
  sys.exit(main())
