"""Low-overhead HUD telemetry publisher for a separate Android renderer.

This process is the remote-HUD path for EON. It deliberately sends only
compact scene data: no framebuffer copies, map decoding, JPEG rendering, or
USB display traffic happens on the EON.
"""

import json
import math
import os
import signal
import socket
import struct
import time

import cereal.messaging as messaging


PORT = 7210
MAP_PORT = 7211
MAP_FILE = "/dev/shm/carrot_navi_map.jpg"
MAP_MAX_BYTES = 2 * 1024 * 1024
FPS = 10


class MapFrameServer(object):
  """Forward the already-compressed TMAP JPEG without decoding it on EON."""

  def __init__(self):
    self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.listener.bind(("0.0.0.0", MAP_PORT))
    self.listener.listen(1)
    self.listener.setblocking(False)
    self.client = None
    self.signature = None

  def _drop_client(self):
    if self.client is not None:
      try:
        self.client.close()
      except Exception:
        pass
    self.client = None
    self.signature = None

  def poll(self):
    if self.client is None:
      try:
        self.client, _ = self.listener.accept()
        # A map frame may be much larger than the telemetry packet. 50 ms was
        # too aggressive on busy EON/S9 Wi-Fi links and caused needless TCP
        # reconnects even though the client was healthy.
        self.client.settimeout(0.5)
        self.signature = None
      except BlockingIOError:
        return

    # The TMAP file is optional and can disappear briefly while carrot-navi
    # replaces it. Missing/in-progress JPEG data is not a TCP failure: keep the
    # S9 connection open and simply wait for the next poll.
    try:
      stat = os.stat(MAP_FILE)
    except (IOError, OSError):
      return

    signature = (getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)), stat.st_size)
    if signature == self.signature or stat.st_size <= 4 or stat.st_size > MAP_MAX_BYTES:
      return

    try:
      with open(MAP_FILE, "rb") as image_file:
        jpeg = image_file.read()
    except (IOError, OSError):
      return

    # A producer can be in the middle of replacing the file when we read it.
    # Ignore that frame without dropping TCP; the next poll will retry it.
    if not (jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")):
      return

    try:
      self.client.sendall(b"MAP1" + struct.pack(">I", len(jpeg)) + jpeg)
      self.signature = signature
    except socket.error:
      self._drop_client()

  def close(self):
    self._drop_client()
    self.listener.close()


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


def _line_points(position, limit=33):
  xs = list(_field(position, "x", []) or [])
  ys = list(_field(position, "y", []) or [])
  count = min(len(xs), len(ys), limit)
  if count < 2:
    return []
  step = max(1, count // 12)
  return [[round(_finite(xs[i]), 2), round(_finite(ys[i]), 2)] for i in range(0, count, step)]


def _model_lines(model, name, confidence_name, confidence_default, invert_confidence=False):
  lines = list(_field(model, name, []) or [])
  confidences = list(_field(model, confidence_name, []) or [])
  result = []
  for index, line in enumerate(lines[:4]):
    points = _line_points(line)
    if len(points) < 2:
      continue
    confidence = confidences[index] if index < len(confidences) else confidence_default
    if invert_confidence:
      confidence = 1.0 - _finite(confidence, 1.0)
    result.append({"p": points, "c": round(max(0.0, min(1.0, _finite(confidence, confidence_default))), 2)})
  return result


def _lead(radar_state, name):
  lead = _field(radar_state, name, None)
  if not bool(_field(lead, "status", False)):
    return None
  return {
    "d": round(max(0.0, _finite(_field(lead, "dRel", 0.0))), 1),
    "y": round(_finite(_field(lead, "yRel", 0.0)), 2),
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
    "leftBlinker": bool(_field(car, "leftBlinker", False)),
    "rightBlinker": bool(_field(car, "rightBlinker", False)),
    "path": _line_points(_field(sm["modelV2"], "position", None)),
    "lanes": _model_lines(sm["modelV2"], "laneLines", "laneLineProbs", 0.0),
    "edges": _model_lines(sm["modelV2"], "roadEdges", "roadEdgeStds", 1.0, True),
    "lead": _lead(sm["radarState"], "leadOne"),
    "lead2": _lead(sm["radarState"], "leadTwo"),
  }


def main():
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
  map_server = MapFrameServer()
  while running[0]:
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
      connected = time.monotonic() - last_ack < 2.0
      map_server.poll()
    except Exception as exc:
      connected = False
      print("remote HUD send failed: %s" % exc, flush=True)
    time.sleep(max(0.0, 1.0 / FPS - (time.monotonic() - started)))
  map_server.close()
  sock.close()


if __name__ == "__main__":
  main()
