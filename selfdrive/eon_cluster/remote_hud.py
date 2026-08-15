"""Low-overhead HUD telemetry publisher for a separate Android renderer.

The process is idle unless EonClusterHudRemote is enabled.  It deliberately
sends only compact scene data: no framebuffer copies, map frames, JPEG work,
or USB traffic happens on the EON.
"""

import json
import math
import signal
import socket
import time

import cereal.messaging as messaging
from common.params import Params


PARAM_ENABLED = "EonClusterHudRemote"
PARAM_CONNECTED = "EonClusterHudRemoteConnected"
PORT = 7210
FPS = 10


def _field(obj, name, default=0):
  try:
    return getattr(obj, name)
  except Exception:
    return default


def _finite(value, default=0.0):
  try:
    value = float(value)
    return value if math.isfinite(value) else default
  except (TypeError, ValueError):
    return default


def _points(model, limit=24):
  position = _field(model, "position", None)
  xs = list(_field(position, "x", []) or [])
  ys = list(_field(position, "y", []) or [])
  count = min(len(xs), len(ys), limit)
  if count < 2:
    return []
  step = max(1, count // 12)
  return [[round(_finite(xs[i]), 2), round(_finite(ys[i]), 2)] for i in range(0, count, step)]


def _lead(radar_state):
  lead = _field(radar_state, "leadOne", None)
  if not bool(_field(lead, "status", False)):
    return None
  return {
    "d": round(max(0.0, _finite(_field(lead, "dRel", 0.0))), 1),
    "v": round(_finite(_field(lead, "vRel", 0.0)) * 3.6, 1),
  }


def _gear(car_state):
  value = str(_field(car_state, "gearShifter", "") or "").split(".")[-1].lower()
  return {"park": "P", "reverse": "R", "neutral": "N", "drive": "D",
          "sport": "S", "low": "L", "brake": "B"}.get(value, "--")


def _set_speed(controls_state, car_control):
  smoother = _field(car_control, "sccSmoother", None)
  value = _field(smoother, "cruiseMaxSpeed", None)
  if value is None:
    value = _field(controls_state, "vCruiseCluster", _field(controls_state, "vCruise", 0.0))
  return max(0, int(round(_finite(value))))


def _packet(sm):
  car = sm["carState"]
  controls = sm["controlsState"]
  road = sm["roadLimitSpeed"]
  device = sm["deviceState"]
  plan = sm["longitudinalPlan"]
  accels = list(_field(plan, "accels", []) or [])
  cam_type = int(_finite(_field(road, "camType", 0)))
  cam_speed = int(_finite(_field(road, "camLimitSpeed", 0)))
  cam_dist = int(_finite(_field(road, "camLimitSpeedLeftDist", 0)))
  if cam_type == 22 or cam_speed <= 0 or cam_dist <= 0:
    cam_speed = int(_finite(_field(road, "sectionLimitSpeed", 0)))
    cam_dist = int(_finite(_field(road, "sectionLeftDist", 0)))
  cpu = list(_field(device, "cpuUsagePercent", []) or [])
  temps = list(_field(device, "cpuTempC", []) or [])
  gap = int(_finite(_field(controls, "longCruiseGap", 0)))
  if not 1 <= gap <= 4:
    gap = int(_finite(_field(car, "cruiseGap", 0)))
  return {
    "v": 1,
    "t": int(time.time() * 1000),
    "speed": int(round(_finite(_field(car, "vEgoCluster", _field(car, "vEgo", 0.0))) * 3.6)),
    "set": _set_speed(controls, sm["carControl"]),
    "enabled": bool(_field(controls, "enabled", False)),
    "gear": _gear(car),
    "gap": gap if 1 <= gap <= 4 else 0,
    "limit": max(0, int(_finite(_field(road, "roadLimitSpeed", 0)))),
    "camera": max(0, cam_speed),
    "cameraDist": max(0, cam_dist),
    "leftBsd": bool(_field(car, "leftBlindspot", False)),
    "rightBsd": bool(_field(car, "rightBlindspot", False)),
    "steer": round(_finite(_field(car, "steeringAngleDeg", 0.0)), 1),
    "accel": round(_finite(accels[0] if accels else 0.0), 2),
    "cpu": int(round(sum(cpu) / len(cpu))) if cpu else 0,
    "temp": round(max(temps), 1) if temps else 0,
    "path": _points(sm["modelV2"]),
    "lead": _lead(sm["radarState"]),
  }


def main():
  params = Params()
  params.put_bool(PARAM_CONNECTED, False)
  running = [True]
  signal.signal(signal.SIGINT, lambda *_: running.__setitem__(0, False))
  signal.signal(signal.SIGTERM, lambda *_: running.__setitem__(0, False))
  sm = messaging.SubMaster(["carState", "carControl", "controlsState", "deviceState",
                            "modelV2", "radarState", "longitudinalPlan", "roadLimitSpeed"])
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  sock.setblocking(False)
  last_ack = 0.0
  connected = False
  while running[0]:
    if not params.get_bool(PARAM_ENABLED):
      if connected:
        connected = False
        params.put_bool(PARAM_CONNECTED, False)
      time.sleep(1.0)
      continue
    started = time.monotonic()
    sm.update(0)
    try:
      sock.sendto(json.dumps(_packet(sm), separators=(",", ":")).encode("utf-8"),
                  ("255.255.255.255", PORT))
      try:
        while True:
          reply, _ = sock.recvfrom(64)
          if reply == b"HUD1":
            last_ack = time.monotonic()
      except (BlockingIOError, socket.error):
        pass
      next_connected = time.monotonic() - last_ack < 2.0
      if next_connected != connected:
        connected = next_connected
        params.put_bool(PARAM_CONNECTED, connected)
    except Exception as exc:
      if connected:
        connected = False
        params.put_bool(PARAM_CONNECTED, False)
      print("remote HUD send failed: %s" % exc, flush=True)
    time.sleep(max(0.0, 1.0 / FPS - (time.monotonic() - started)))
  params.put_bool(PARAM_CONNECTED, False)
  sock.close()


if __name__ == "__main__":
  main()
