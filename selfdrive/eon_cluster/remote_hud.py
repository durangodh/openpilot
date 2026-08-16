"""Low-overhead HUD telemetry publisher for a separate Android renderer.

This process is the remote-HUD path for EON. It deliberately sends only
compact scene data and already-compressed TMAP assets: no framebuffer copies,
map decoding, JPEG rendering, or USB display traffic happens on the EON.
"""

import base64
import json
import math
import os
import signal
import socket
import struct
import time

import cereal.messaging as messaging
from common.params import Params


PORT = 7210
MAP_PORT = 7211
MAP_FILE = "/dev/shm/carrot_navi_map.jpg"
TBT_CURRENT_FILE = "/dev/shm/carrot_navi_tbt_current_full.png"
TBT_NEXT_FILE = "/dev/shm/carrot_navi_tbt_next.png"
LANE_BOTTOM_FILE = "/dev/shm/carrot_navi_lane_bottom.png"
NAVI_STATE = "/dev/shm/carrot_navi_route.json"
MAP_MAX_BYTES = 2 * 1024 * 1024
OVERLAY_MAX_BYTES = 512 * 1024
MAP_KEEPALIVE_S = 1.0
NAVI_MAX_AGE_MS = 35000
NAVI_GUIDANCE_MAX_AGE_MS = 3000
MAP_IDLE_JPEG = base64.b64decode(
  "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGDEjJR0oOjM9PDkzODdASFxOQERXRTc4UG1RV19iZ2hnPk1xeXBkeFxlZ2P/2wBDARESEhgVGC8aGi9jQjhCY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2NjY2P/wAARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDz+iiigD//2Q==")
FPS = 10
PARAM_ENABLED = "EonClusterHud"
PARAM_OUTPUT_MODE = "EonClusterHudOutputMode"
PARAM_CONNECTED = "EonClusterHudConnected"
PARAM_ATC_MODE = "CarrotAutoTurnControl"
_NAVI_CACHE = {"signature": None, "state": {}}

# One-time S9 APK support for runtime layout tuning.  After the compatible APK
# is installed, ordinary HUD position/size/color tweaks only require changing
# this dictionary on EON; the values ride along with the existing 10 Hz JSON.
# Per-element positioning uses <name>Dx / <name>Dy / <name>Scale.
REMOTE_LAYOUT = {
  "driveBg": 0xEFF1F2,
  "roadTop": 0xE2E5E7,
  "roadBottom": 0xD8DCDF,
  "pathColor": 0x187EE0,
  "lightsDx": 0, "lightsDy": 0, "lightsScale": 1.0,
  "prndDx": 0, "prndDy": 0, "prndScale": 1.0,
  "speedDx": 0, "speedDy": 0, "speedScale": 1.0,
  "wheelDx": 0, "wheelDy": 0, "wheelScale": 1.0,
  "setDx": 0, "setDy": 0, "setScale": 1.0,
  "cameraDx": 0, "cameraDy": 0, "cameraScale": 1.0,
  "leadDx": 0, "leadDy": 0, "leadScale": 1.0,
  "tpmsDx": 0, "tpmsDy": 0, "tpmsScale": 1.0,
  "atcDx": 0, "atcDy": 0, "atcScale": 1.0,
  "systemDx": 0, "systemDy": 0, "systemScale": 1.0,
  "modeX": 742, "modeY": 116, "modeSize": 29,
  "etaRight": 620, "etaY": 116, "etaTimeSize": 27, "etaLabelSize": 14, "etaGap": 8,
  "tbt1Dx": 0, "tbt1Dy": 0, "tbt1Scale": 1.0,
  "tbt2Dx": 0, "tbt2Dy": 0, "tbt2Scale": 1.0,
  "laneDx": 0, "laneDy": 0, "laneScale": 1.0,
}


class MapFrameServer(object):
  """Forward native compressed TMAP map/guidance assets without decoding."""

  ASSETS = (
    (b"MAP1", MAP_FILE, MAP_MAX_BYTES, MAP_IDLE_JPEG),
    (b"TBT1", TBT_CURRENT_FILE, OVERLAY_MAX_BYTES, b""),
    (b"TBT2", TBT_NEXT_FILE, OVERLAY_MAX_BYTES, b""),
    (b"LANE", LANE_BOTTOM_FILE, OVERLAY_MAX_BYTES, b""),
  )

  def __init__(self):
    self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    self.listener.bind(("0.0.0.0", MAP_PORT))
    self.listener.listen(1)
    self.listener.setblocking(False)
    self.client = None
    self.signatures = {}
    self.cached = {tag: fallback for tag, _, _, fallback in self.ASSETS}
    self.pending = set(tag for tag, _, _, _ in self.ASSETS)
    self.last_send = 0.0

  def _drop_client(self):
    if self.client is not None:
      try:
        self.client.close()
      except Exception:
        pass
    self.client = None
    self.last_send = 0.0
    self.pending = set(tag for tag, _, _, _ in self.ASSETS)

  @staticmethod
  def _valid_image(data):
    if not data:
      return False
    if data.startswith(b"\xff\xd8"):
      return data.endswith(b"\xff\xd9")
    return data.startswith(b"\x89PNG\r\n\x1a\n")

  def _refresh_asset(self, tag, path, maximum, fallback):
    try:
      stat = os.stat(path)
      signature = (getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)), stat.st_size)
    except (IOError, OSError):
      if self.signatures.get(tag) is not None or self.cached.get(tag, fallback) != fallback:
        self.signatures[tag] = None
        self.cached[tag] = fallback
        self.pending.add(tag)
      return

    if signature == self.signatures.get(tag):
      return
    if stat.st_size <= 4 or stat.st_size > maximum:
      return
    try:
      with open(path, "rb") as image_file:
        data = image_file.read()
    except (IOError, OSError):
      return
    if not self._valid_image(data):
      return
    self.signatures[tag] = signature
    self.cached[tag] = data
    self.pending.add(tag)

  def _send_asset(self, tag):
    payload = self.cached.get(tag, b"")
    self.client.sendall(tag + struct.pack(">I", len(payload)) + payload)

  def poll(self):
    if self.client is None:
      try:
        self.client, _ = self.listener.accept()
        self.client.settimeout(0.5)
        self.last_send = 0.0
        self.pending = set(tag for tag, _, _, _ in self.ASSETS)
      except BlockingIOError:
        return

    for asset in self.ASSETS:
      self._refresh_asset(*asset)

    now = time.monotonic()
    if now - self.last_send >= MAP_KEEPALIVE_S:
      self.pending.add(b"MAP1")

    if not self.pending:
      return
    try:
      for tag, _, _, _ in self.ASSETS:
        if tag in self.pending:
          self._send_asset(tag)
      self.pending.clear()
      self.last_send = now
    except socket.error:
      self._drop_client()

  def close(self):
    self._drop_client()
    self.listener.close()

  def set_inactive(self):
    self._drop_client()


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


def _param_int(params, key, default=0, minimum=0, maximum=999):
  try:
    raw = params.get(key)
    value = int(raw) if raw is not None else default
  except (TypeError, ValueError):
    value = default
  return max(minimum, min(maximum, value))


def _remote_output_enabled(params):
  if not params.get_bool(PARAM_ENABLED):
    return False
  try:
    raw = params.get(PARAM_OUTPUT_MODE)
    return int(raw) != 0 if raw is not None else True
  except (TypeError, ValueError):
    return True


def _migrate_legacy_remote_mode(params):
  if params.get(PARAM_OUTPUT_MODE) is None:
    params.put(PARAM_OUTPUT_MODE, "1")
    params.put_bool(PARAM_ENABLED, True)


def _publish_connected(params, state, value):
  if state[0] is value:
    return
  try:
    params.put_bool(PARAM_CONNECTED, value)
    state[0] = value
  except Exception as exc:
    print("remote HUD connected flag failed: %s" % exc, flush=True)


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
  label = {"park": "P", "reverse": "R", "neutral": "N", "drive": "D",
           "sport": "S", "low": "L", "brake": "B"}.get(value)
  if label:
    return label
  step = int(_finite(_field(car_state, "gearStep", 0)))
  return str(step) if step > 0 else "--"


def _set_speed(controls_state, car_control):
  smoother = _field(car_control, "sccSmoother", None)
  value = _field(smoother, "cruiseMaxSpeed", None)
  if value is None:
    value = _field(controls_state, "vCruiseCluster", _field(controls_state, "vCruise", 0.0))
  return max(0, int(round(_finite(value))))


def _read_navi_summary():
  try:
    stat = os.stat(NAVI_STATE)
    signature = (getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1e9)), stat.st_size)
  except (IOError, OSError):
    _NAVI_CACHE["signature"] = None
    _NAVI_CACHE["state"] = {}
    return {}

  if signature != _NAVI_CACHE["signature"]:
    try:
      with open(NAVI_STATE, "r") as state_file:
        state = json.load(state_file)
    except (IOError, ValueError):
      return _NAVI_CACHE["state"]
    _NAVI_CACHE["signature"] = signature
    _NAVI_CACHE["state"] = state
  else:
    state = _NAVI_CACHE["state"]

  now_ms = int(time.time() * 1000)
  updated_at = int(state.get("updated_at_ms", 0) or 0)
  if updated_at <= 0 or abs(now_ms - updated_at) > NAVI_MAX_AGE_MS:
    return {}

  route = state.get("route") or {}
  guide = state.get("guidance_current") or {}
  vehicle = state.get("vehicle") or {}
  stream_times = state.get("stream_updated_at_ms") or {}
  guidance_at = int(stream_times.get("guidance_current", updated_at) or 0)
  guidance_live = -5000 <= now_ms - guidance_at <= NAVI_GUIDANCE_MAX_AGE_MS
  status = state.get("navigation_status") or {}
  active = True
  if isinstance(status, dict):
    for key in ("active", "is_active", "isActive", "navigating", "is_navigating", "isNavigating",
                "route_active", "routeActive"):
      if key in status and not bool(status.get(key)):
        active = False
    status_text = str(status.get("state", status.get("status", "")) or "").lower()
    if status_text in ("idle", "inactive", "off", "stopped", "ended", "none"):
      active = False
  try:
    remain_distance = float(route.get("remain_distance_m", 0) or 0)
  except (TypeError, ValueError):
    remain_distance = 0.0
  active = active and (remain_distance > 0 or bool(guide))
  if not active:
    return {"active": False}

  try:
    turn_type = int(guide.get("turn_type", 0) or 0)
    turn_distance = max(0, int(round(float(guide.get("distance_m", 0) or 0))))
    remain_time = max(0, int(route.get("remain_time_sec", 0) or 0))
  except (TypeError, ValueError):
    turn_type, turn_distance, remain_time = 0, 0, 0
  title = str(guide.get("main_text") or guide.get("road_name") or vehicle.get("road_name") or "")
  return {
    "active": True,
    "guidanceLive": bool(guidance_live),
    "turnType": turn_type,
    "turnDist": turn_distance,
    "remainTime": remain_time,
    "remainDist": max(0, int(round(remain_distance))),
    "title": title[:48],
  }


def _packet(sm, atc_mode):
  car = sm["carState"]
  controls = sm["controlsState"]
  road = sm["roadLimitSpeed"]
  device = sm["deviceState"]
  plan = sm["longitudinalPlan"]
  accels = list(_field(plan, "accels", []) or [])
  cam_type = int(_finite(_field(road, "camType", 0)))
  cam_speed = int(_finite(_field(road, "camLimitSpeed", 0)))
  cam_dist = int(_finite(_field(road, "camLimitSpeedLeftDist", 0)))
  camera_section = False
  if cam_type == 22 or cam_speed <= 0 or cam_dist <= 0:
    cam_speed = int(_finite(_field(road, "sectionLimitSpeed", 0)))
    cam_dist = int(_finite(_field(road, "sectionLeftDist", 0)))
    camera_section = cam_speed > 0 and cam_dist > 0
  cpu = list(_field(device, "cpuUsagePercent", []) or [])
  temps = list(_field(device, "cpuTempC", []) or [])
  cpu_avg = (sum(float(v) for v in cpu) / len(cpu)) if cpu else 0.0
  temp_avg = (sum(float(v) for v in temps) / len(temps)) if temps else 0.0
  engine_temp = _finite(_field(car, "engineOilTempC", -1000.0), -1000.0)
  coolant_temp = _finite(_field(car, "engineCoolantTempC", -1000.0), -1000.0)
  engine_temp = engine_temp if -50.0 <= engine_temp <= 200.0 else None
  coolant_temp = coolant_temp if -50.0 <= coolant_temp <= 200.0 else None
  gap = int(_finite(_field(controls, "longCruiseGap", 0)))
  if not 1 <= gap <= 4:
    gap = int(_finite(_field(car, "cruiseGap", 0)))
  mode = int(_finite(_field(controls, "myDrivingMode", 3), 3))
  if not 1 <= mode <= 4:
    mode = 3
  tpms = _field(car, "tpms", None)
  navi = _read_navi_summary()
  return {
    "v": 4,
    "t": int(time.time() * 1000),
    "layout": REMOTE_LAYOUT,
    "speed": int(round(_finite(_field(car, "vEgoCluster", _field(car, "vEgo", 0.0))) * 3.6)),
    "set": _set_speed(controls, sm["carControl"]),
    "enabled": bool(_field(controls, "enabled", False)),
    "gear": _gear(car),
    "gap": gap if 1 <= gap <= 4 else 0,
    "drivingMode": mode,
    "limit": max(0, int(_finite(_field(road, "roadLimitSpeed", 0)))),
    "camera": max(0, cam_speed),
    "cameraDist": max(0, cam_dist),
    "cameraSection": bool(camera_section),
    "leftBsd": bool(_field(car, "leftBlindspot", False)),
    "rightBsd": bool(_field(car, "rightBlindspot", False)),
    "steer": round(_finite(_field(car, "steeringAngleDeg", 0.0)), 1),
    "accel": round(_finite(accels[0] if accels else 0.0), 2),
    "cpu": int(round(cpu_avg)),
    "temp": round(temp_avg, 1),
    "system": {
      "cpu": round(cpu_avg, 1),
      "temp": round(temp_avg, 1),
      "engineTemp": engine_temp,
      "coolantTemp": coolant_temp,
      "cores": [round(float(v), 1) for v in cpu[:8]],
    },
    "leftBlinker": bool(_field(car, "leftBlinker", False)),
    "rightBlinker": bool(_field(car, "rightBlinker", False)),
    "lowBeam": bool(_field(car, "lowBeam", False)),
    "highBeam": bool(_field(car, "highBeam", False)),
    "frontFog": bool(_field(car, "frontFogLight", False)),
    "distanceToEmpty": round(_finite(_field(car, "distanceToEmptyKm", -1.0)), 1),
    "tpms": {
      "fl": _finite(_field(tpms, "fl", -1.0), -1.0),
      "fr": _finite(_field(tpms, "fr", -1.0), -1.0),
      "rl": _finite(_field(tpms, "rl", -1.0), -1.0),
      "rr": _finite(_field(tpms, "rr", -1.0), -1.0),
    },
    "atcMode": int(atc_mode),
    "navi": navi,
    "path": _line_points(_field(sm["modelV2"], "position", None)),
    "lanes": _model_lines(sm["modelV2"], "laneLines", "laneLineProbs", 0.0),
    "edges": _model_lines(sm["modelV2"], "roadEdges", "roadEdgeStds", 1.0, True),
    "lead": _lead(sm["radarState"], "leadOne"),
    "lead2": _lead(sm["radarState"], "leadTwo"),
  }


def main():
  params = Params()
  _migrate_legacy_remote_mode(params)
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
  published = [None]
  map_server = MapFrameServer()
  atc_mode = _param_int(params, PARAM_ATC_MODE, 0, 0, 3)
  next_param_read = 0.0
  while running[0]:
    started = time.monotonic()
    if not _remote_output_enabled(params):
      connected = False
      last_ack = 0.0
      _publish_connected(params, published, False)
      map_server.set_inactive()
      time.sleep(0.25)
      continue
    if started >= next_param_read:
      atc_mode = _param_int(params, PARAM_ATC_MODE, 0, 0, 3)
      next_param_read = started + 1.0
    sm.update(0)
    try:
      sock.sendto(json.dumps(_packet(sm, atc_mode), separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
                  ("255.255.255.255", PORT))
      try:
        while True:
          reply, _ = sock.recvfrom(64)
          if reply == b"HUD1":
            last_ack = time.monotonic()
      except (BlockingIOError, socket.error):
        pass
      connected = time.monotonic() - last_ack < 2.0
      _publish_connected(params, published, connected)
      map_server.poll()
    except Exception as exc:
      connected = False
      _publish_connected(params, published, False)
      print("remote HUD send failed: %s" % exc, flush=True)
    time.sleep(max(0.0, 1.0 / FPS - (time.monotonic() - started)))
  _publish_connected(params, published, False)
  map_server.close()
  sock.close()


if __name__ == "__main__":
  main()
