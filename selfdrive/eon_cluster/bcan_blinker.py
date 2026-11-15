#!/usr/bin/env python3
"""EON side of the Genesis DH B-CAN turn-signal tap (see bcan_blinker_core.py).

When openpilot starts a lane change on its own (Bluetooth remote, NOO) the ESP32 on the
B-CAN is asked for one stock one-touch tap, so the exterior lamps flash 5 times. A lane
change the driver started with the lever is left alone. The ESP32's link state and the
last tap result go to STATUS_FILE for the EON driving screen and the S9 HUD.
Enabled by the DhBcanBlinker param.
"""
import os
import random
import socket
import time

import cereal.messaging as messaging
from cereal import log
from common.params import Params
from selfdrive.eon_cluster.bcan_blinker_core import (COMMAND_PORT, COMMAND_REPEATS, LINK_NONE, LINK_OK, RESULT_TEXT,
                                                     STATUS_FILE, STATUS_PORT, LaneTapPlanner, command_packet,
                                                     link_state, parse_status, write_status)

LaneChangeState = log.LateralPlan.LaneChangeState
LaneChangeDirection = log.LateralPlan.LaneChangeDirection
CHANGING = (LaneChangeState.preLaneChange, LaneChangeState.laneChangeStarting)
LOOP_S = 0.05
STATUS_WRITE_S = 0.5
PARAM_CHECK_S = 2.0
# Decision log for road tests (cat /data/bcan_blinker.log): every lane change, ESP32 link change
# and tap result, one line each. Kept under LOG_MAX_BYTES (the old half goes to .1).
LOG_FILE = "/data/bcan_blinker.log"
LOG_MAX_BYTES = 256 * 1024


class DecisionLog:
  def __init__(self, path=LOG_FILE, max_bytes=LOG_MAX_BYTES):
    self.path = path
    self.max_bytes = max_bytes

  def write(self, text):
    try:
      if os.path.exists(self.path) and os.path.getsize(self.path) > self.max_bytes:
        os.replace(self.path, self.path + ".1")
      with open(self.path, "a") as f:
        f.write(time.strftime("%m-%d %H:%M:%S ") + text + "\n")
    except OSError:
      pass


def open_status_socket():
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  sock.bind(("", STATUS_PORT))
  sock.setblocking(False)
  return sock


def main():
  params = Params()
  sm = messaging.SubMaster(['lateralPlan', 'carState', 'controlsState'])
  rx = open_status_socket()
  tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  planner = LaneTapPlanner()

  enabled = False
  next_param_check = 0.0
  next_status_write = 0.0
  status = None
  status_mono = None
  esp_ip = None
  seq = random.randint(1, 30000)
  event = None
  dlog = DecisionLog()
  dlog.write("start")
  prev_enabled = None
  prev_link_key = None
  prev_lc = None

  while True:
    now = time.monotonic()
    if now >= next_param_check:
      enabled = params.get_bool("DhBcanBlinker")
      next_param_check = now + PARAM_CHECK_S
      if enabled != prev_enabled:
        dlog.write(f"DhBcanBlinker {'on' if enabled else 'off'}")
        prev_enabled = enabled

    while True:
      try:
        data, addr = rx.recvfrom(512)
      except (BlockingIOError, InterruptedError):
        break
      except OSError:
        break
      parsed = parse_status(data)
      if parsed is None:
        continue
      status, status_mono, esp_ip = parsed, now, addr[0]
      if event is not None and event.get("pending") and parsed["seq"] == event["seq"] and parsed["res"]:
        event["pending"] = False
        event["ok"] = parsed["res"] == "ok"
        event["why"] = RESULT_TEXT.get(parsed["res"], parsed["res"])
        dlog.write(f"ESP32 answer seq {parsed['seq']}: {parsed['res']} (lamp {parsed['lamp']}, bcan {parsed['bcan']}, err {parsed['err']})")

    if not enabled:
      planner = LaneTapPlanner()
      time.sleep(0.25)
      continue

    sm.update(0)
    age = None if status_mono is None else now - status_mono
    link = link_state(status, age)
    link_key = (link, status["lamp"] if status and link != LINK_NONE else None)
    if link_key != prev_link_key:
      if status is not None and link != LINK_NONE:
        dlog.write(f"ESP32 link {link} ({esp_ip}, rssi {status['rssi']}, bcan {status['bcan']}, "
                   f"lamp {status['lamp']}, err {status['err']})")
      else:
        dlog.write(f"ESP32 link {link} (no status for {'ever' if age is None else f'{age:.1f} s'})")
      prev_link_key = link_key

    plan = sm['lateralPlan']
    car = sm['carState']
    changing = (sm['controlsState'].enabled and sm.alive['lateralPlan'] and
                plan.laneChangeState in CHANGING)
    direction = {LaneChangeDirection.left: "L", LaneChangeDirection.right: "R"}.get(plan.laneChangeDirection)
    side = planner.update(now, changing, direction, car.leftBlinker, car.rightBlinker)
    lc = (int(plan.laneChangeState), direction) if sm.alive['lateralPlan'] else None
    if lc != prev_lc and lc is not None and lc[0] != LaneChangeState.off:
      dlog.write(f"lane change state {lc[0]} dir {direction} engaged {sm['controlsState'].enabled} "
                 f"lamps L{int(car.leftBlinker)} R{int(car.rightBlinker)} link {link} -> "
                 f"{'tap ' + side if side else 'driver lever (no tap)' if planner.driver_owned else 'no tap'}")
    prev_lc = lc
    if side is not None:
      seq = (seq % 30000) + 1
      wall = time.time()
      if link == LINK_OK and esp_ip:
        packet = command_packet(seq, side)
        for _ in range(COMMAND_REPEATS):
          try:
            tx.sendto(packet, (esp_ip, COMMAND_PORT))
          except OSError:
            pass
          time.sleep(0.02)
        event = {"seq": seq, "dir": side, "wall": wall, "ok": False, "why": "응답 대기", "pending": True}
        dlog.write(f"tap {side} seq {seq} sent to {esp_ip}")
      else:
        why = "ESP32 없음" if link == LINK_NONE else "B-CAN 없음" if link != LINK_OK else "주소 없음"
        event = {"seq": seq, "dir": side, "wall": wall, "ok": False, "why": why, "pending": False}
        dlog.write(f"tap {side} seq {seq} NOT sent: {why}")

    if now >= next_status_write:
      next_status_write = now + STATUS_WRITE_S
      payload = {"wall": time.time(), "link": link, "rssi": status["rssi"] if status and link != LINK_NONE else 0}
      if event is not None:
        payload["event"] = {k: v for k, v in event.items() if k != "pending"}
      try:
        write_status(STATUS_FILE, payload)
      except OSError:
        pass

    time.sleep(LOOP_S)


if __name__ == "__main__":
  main()
