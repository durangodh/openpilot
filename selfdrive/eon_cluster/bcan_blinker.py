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
from selfdrive.eon_cluster.bcan_blinker_core import (COMMAND_PORT, COMMAND_REPEATS, LINK_NONE, LINK_OK, RESULT_TEXT, NooHoldPlanner,
                                                     STATUS_FILE, STATUS_PORT, BlinkCancelPlanner, BlinkRequestSource, LaneCrossDetector, hold_press_action, stopped_blink_action, HOLD_KEEPALIVE_S, HOLD_MAX_S, LaneTapPlanner, command_packet, lane_direction,
                                                     link_state, parse_status, write_status, WindowRequestSource)

LaneChangeState = log.LateralPlan.LaneChangeState
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
  sm = messaging.SubMaster(['lateralPlan', 'carState', 'controlsState', 'modelV2'])
  rx = open_status_socket()
  tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  planner = LaneTapPlanner()
  noo_hold = NooHoldPlanner()
  canceller = BlinkCancelPlanner()
  crosser = LaneCrossDetector()
  cancel_enabled = False
  cancel_seq = None
  blink_requests = BlinkRequestSource()
  window_requests = WindowRequestSource()
  call_long_seen = None   # ESP32 "cl": steering wheel call button long presses
  window_seq = None
  own_tap_time = None   # monotonic time of our last tap (a cancel clears it)
  hold_enabled = False
  hold_side = None      # 'L' / 'R' while the ESP32 holds the blinker for us
  hold_start = hold_keep = 0.0
  hold_seq = None
  keep_sent = keep_skipped = 0

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
      cancel_enabled = params.get_bool("DhBcanBlinkerCancel")
      hold_enabled = params.get_bool("DhBcanBlinkerHold")
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
      # The ESP32 reports whether it still holds and why the last hold ended (firmware 2026-11-16c).
      if hold_side is not None and parsed.get("hold") == 0 and now - hold_start > 1.5:
        dlog.write(f"hold {hold_side} ended by ESP32: {parsed.get('hend') or '?'} "
                   f"(keepalives: EON sent {keep_sent}, not sent {keep_skipped}, ESP32 got {parsed.get('kc')}; "
                   f"ESP32 packets {parsed.get('rx')}, last '{parsed.get('lc')}', failed re-taps {parsed.get('hf')})")
        hold_side = None
      if hold_seq is not None and parsed["seq"] == hold_seq and parsed["res"]:
        # A failed first tap does not end the hold on the ESP32 (it taps again); only its
        # hold flag does.
        if parsed["res"] != "ok" and hold_side is not None and parsed.get("hold") == 0:
          dlog.write(f"hold {hold_side} ended by ESP32: {parsed['res']}")
          hold_side = None
        hold_seq = None
      # Call button press (firmware 2026-11-22b): open the HUD AI voice assistant, like the
      # remote's voice button. The first count after (re)connecting is only remembered.
      cl = parsed.get("cl")
      if cl is not None:
        if call_long_seen is not None and cl != call_long_seen and enabled:
          from selfdrive.eon_cluster.hud_remote import VOICE_FILE, _wake_remote_hud, atomic_json, read_json
          previous = read_json(VOICE_FILE)
          vseq = previous.get("seq", 0) if isinstance(previous, dict) else 0
          vseq = (vseq + 1) % 1000000 if isinstance(vseq, int) else 1
          try:
            atomic_json(VOICE_FILE, {"seq": vseq, "ts": time.monotonic(), "mode": "ai"})
            _wake_remote_hud()
            dlog.write(f"call button: AI voice ({cl})")
          except OSError as e:
            dlog.write(f"call button voice failed: {e}")
        call_long_seen = cl
      if window_seq is not None and parsed["seq"] == window_seq and parsed["res"]:
        dlog.write(f"ESP32 answer window seq {window_seq}: {parsed['res']}")
        window_seq = None
      if cancel_seq is not None and parsed["seq"] == cancel_seq and parsed["res"]:
        dlog.write(f"ESP32 answer cancel seq {cancel_seq}: {parsed['res']} (lamp {parsed['lamp']})")
        cancel_seq = None
      if event is not None and event.get("pending") and parsed["seq"] == event["seq"] and parsed["res"]:
        event["pending"] = False
        event["ok"] = parsed["res"] == "ok"
        event["why"] = RESULT_TEXT.get(parsed["res"], parsed["res"])
        dlog.write(f"ESP32 answer seq {parsed['seq']}: {parsed['res']} (lamp {parsed['lamp']}, bcan {parsed['bcan']}, err {parsed['err']})")

    if not enabled:
      planner = LaneTapPlanner()
      # Let an already-owned hold reach the stop path below. A disabled service
      # must never continue transmitting its automatic keepalive.
      if hold_side is None:
        noo_hold = NooHoldPlanner()
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
    changing = (enabled and sm['controlsState'].enabled and sm.alive['lateralPlan'] and
                plan.laneChangeState in CHANGING)
    direction = lane_direction(plan.laneChangeDirection)
    # Remote lane button = the lever: tap the exterior blinker at any speed (a second press while
    # ours still blinks and no lane change runs ends it). A lane change that follows counts this
    # tap as its first (planner) and may cancel it at the line crossing (canceller).
    lane_change_active = sm.alive['lateralPlan'] and str(plan.laneChangeState) != "off"
    request = blink_requests.poll() if enabled else None
    if hold_enabled or hold_side is not None:
      lit = "L" if car.leftBlinker and not car.rightBlinker else \
            "R" if car.rightBlinker and not car.leftBlinker else None
      action = hold_press_action(request, hold_side, lit if sm.alive['carState'] else None)
    else:
      action = stopped_blink_action(request, own_tap_time, now, lane_change_active)
    if not enabled and hold_side is not None:
      action = "C"
    # The planner publishes the current NOO turn/one-lane request; remote lane
    # requests are not included in nooLaneChangeDirection. Stale plans and a
    # disengagement release only the hold this policy started.
    noo_direction = 0
    if enabled and sm.alive['lateralPlan'] and sm.valid['lateralPlan'] and \
       sm.alive['carState'] and sm.valid['carState'] and \
       sm.alive['controlsState'] and sm.valid['controlsState'] and sm['controlsState'].enabled and \
       not car.brakePressed and str(car.gearShifter) == "drive":
      noo_direction = plan.nooTurnDirection or plan.nooLaneChangeDirection
    desired_noo = {-1: "L", 1: "R"}.get(noo_direction)
    nav_action = noo_hold.update(desired_noo, hold_side,
                                 enabled and hold_enabled and link == LINK_OK and bool(esp_ip),
                                 remote_request=request,
                                 lamps_on=car.leftBlinker or car.rightBlinker)
    if action is None:
      action = nav_action
    if action is None and hold_side is not None and now - hold_keep >= HOLD_KEEPALIVE_S:
      if now - hold_start > HOLD_MAX_S or not hold_enabled or not enabled:
        action = "C"
        dlog.write(f"hold {hold_side} stop: {'max time' if hold_enabled else 'setting off'}")
      else:
        action = "K" + hold_side   # keepalive: refreshes the ESP32 hold, never restarts it
    press_tap = None
    if action is not None:
      seq = (seq % 30000) + 1
      reachable = link == LINK_OK or (link != LINK_NONE and action[0] in ("C", "K"))
      if reachable and esp_ip:
        packet = command_packet(seq, action)
        for _ in range(COMMAND_REPEATS):
          try:
            tx.sendto(packet, (esp_ip, COMMAND_PORT))
          except OSError:
            pass
          time.sleep(0.02)
        if action == "C":
          cancel_seq = seq
          own_tap_time = None
          hold_side = None
          dlog.write(f"remote: cancel seq {seq} sent to {esp_ip}")
        elif action in ("HL", "HR", "KL", "KR"):
          hold_seq = seq
          hold_keep = now
          if action[0] == "K":
            keep_sent += 1
          if hold_side != action[1]:
            hold_side, hold_start = action[1], now
            keep_sent = keep_skipped = 0
            own_tap_time = now
            press_tap = action[1]
            planner.note_external_tap(now, action[1])
            event = {"seq": seq, "dir": action[1], "wall": time.time(), "ok": False, "why": "응답 대기", "pending": True}
            dlog.write(f"remote: hold {action[1]} seq {seq} sent to {esp_ip} (v {car.vEgo:.1f} m/s)")
        else:
          own_tap_time = now
          press_tap = action
          planner.note_external_tap(now, action)
          event = {"seq": seq, "dir": action, "wall": time.time(), "ok": False, "why": "응답 대기", "pending": True}
          dlog.write(f"remote: tap {action} seq {seq} sent to {esp_ip} (v {car.vEgo:.1f} m/s)")
      elif action.startswith("K"):
        keep_skipped += 1
        hold_keep = now
      else:
        dlog.write(f"remote: {action} NOT sent: link {link}")
        if action == "C":
          # Stop renewing our local lease even if the stop packet cannot get
          # through. The ESP32's existing keepalive timeout then releases it.
          hold_side = hold_seq = own_tap_time = None

    side = planner.update(now, changing, direction, car.leftBlinker, car.rightBlinker)
    if hold_side is not None:
      side = None  # the hold already signals; do not inject separate one-touch taps
    try:  # the log must never take the process down
      lc = (str(plan.laneChangeState), direction) if sm.alive['lateralPlan'] else None
      if lc != prev_lc and lc is not None and lc[0] != "off":
        dlog.write(f"lane change {lc[0]} dir {direction} engaged {sm['controlsState'].enabled} "
                   f"lamps L{int(car.leftBlinker)} R{int(car.rightBlinker)} link {link} -> "
                   f"{'tap ' + side if side else 'driver lever (no tap)' if planner.driver_owned else 'no tap'}")
      prev_lc = lc
    except Exception as e:  # pylint: disable=broad-except
      dlog.write(f"log error {e!r}")
    tap_sent = None
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
        tap_sent = side
        own_tap_time = now
      else:
        why = "ESP32 없음" if link == LINK_NONE else "B-CAN 없음" if link != LINK_OK else "주소 없음"
        event = {"seq": seq, "dir": side, "wall": wall, "ok": False, "why": why, "pending": False}
        dlog.write(f"tap {side} seq {seq} NOT sent: {why}")

    # Lane change done: stop the remaining flashes of our one-touch (DhBcanBlinkerCancel).
    state_name = str(plan.laneChangeState) if sm.alive['lateralPlan'] else "off"
    crossed = False
    if state_name == "laneChangeStarting" and direction in ("L", "R") and sm.alive['modelV2'] and sm.updated['modelV2']:
      try:
        idx = 1 if direction == "L" else 2
        line = sm['modelV2'].laneLines[idx]
        crossed = crosser.update(abs(float(line.y[0])), float(sm['modelV2'].laneLineProbs[idx]))
      except (IndexError, AttributeError, TypeError, ValueError):
        crosser.reset()
    elif state_name != "laneChangeStarting":
      crosser.reset()
    if crossed:
      dlog.write(f"lane line crossed ({direction})")
    if canceller.update(now, state_name, tap_sent or press_tap, crossed) and cancel_enabled:
      if link == LINK_OK and esp_ip:
        seq = (seq % 30000) + 1
        packet = command_packet(seq, "C")
        for _ in range(COMMAND_REPEATS):
          try:
            tx.sendto(packet, (esp_ip, COMMAND_PORT))
          except OSError:
            pass
          time.sleep(0.02)
        cancel_seq = seq  # the screens keep showing the tap; the answer goes to the log only
        own_tap_time = None
        hold_side = None   # the lane change is done: a held blinker ends with it
        dlog.write(f"cancel seq {seq} sent to {esp_ip} (lane change finishing)")
      else:
        dlog.write(f"cancel NOT sent: link {link}")

    # Power windows (selfdrive/debug/dh_window.py, remote, voice) go to the same ESP32.
    window_cmd = window_requests.poll()
    if window_cmd is not None:
      if link == LINK_OK and esp_ip:
        seq = (seq % 30000) + 1
        packet = command_packet(seq, window_cmd)
        for _ in range(COMMAND_REPEATS):
          try:
            tx.sendto(packet, (esp_ip, COMMAND_PORT))
          except OSError:
            pass
          time.sleep(0.02)
        window_seq = seq
        dlog.write(f"window {window_cmd} seq {seq} sent to {esp_ip}")
      else:
        dlog.write(f"window {window_cmd} NOT sent: link {link}")

    if now >= next_status_write:
      next_status_write = now + STATUS_WRITE_S
      payload = {"wall": time.time(), "link": link, "rssi": status["rssi"] if status and link != LINK_NONE else 0}
      if esp_ip:
        payload["ip"] = esp_ip   # EON Web links the board's update page with it
      if event is not None:
        payload["event"] = {k: v for k, v in event.items() if k != "pending"}
      try:
        write_status(STATUS_FILE, payload)
      except OSError:
        pass

    time.sleep(LOOP_S)


if __name__ == "__main__":
  main()
