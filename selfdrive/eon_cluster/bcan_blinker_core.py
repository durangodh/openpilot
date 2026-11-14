"""Genesis DH B-CAN turn-signal tap (ESP32, tools/dh_bcan_blinker): protocol and lane-change logic.

openpilot lane changes from the Bluetooth remote and NOO raise only a virtual blinker, so the
exterior lamps stay dark. An ESP32 on the DH B-CAN (M01 multifunction switch, 100 kbit/s) sends
one stock-like one-touch lever tap (0x138) on request; the body controller then flashes the
exterior lamps 5 times (~3.5 s), exactly like the driver's one-touch. Verified on the car
2026-11-14: one tap = 5 exterior flashes, no warnings.

EON and the ESP32 share the phone hotspot (S20, 2.4 GHz):
  ESP32 -> EON  UDP STATUS_PORT, broadcast every second and after each tap:
                {"t": "dhbl", "v": 1, "rssi": -55, "bcan": 1, "lamp": 0, "err": 0, "seq": 3, "res": "ok"}
  EON -> ESP32  UDP COMMAND_PORT, unicast to the status sender: "DHBL1 <seq> L" / "DHBL1 <seq> R"
"""
import json
import math
import os
import time

STATUS_PORT = 7291
COMMAND_PORT = 7290
COMMAND_MAGIC = "DHBL1"
STATUS_FILE = "/dev/shm/dh_bcan_blinker.json"

# ESP32 link for the HUD: 0 no ESP32, 1 ESP32 without B-CAN, 2 ready, 3 B-CAN error.
LINK_NONE, LINK_NO_BCAN, LINK_OK, LINK_ERROR = 0, 1, 2, 3
LINK_STALE_S = 3.0
STATUS_STALE_S = 3.0
EVENT_SHOW_S = 3.0

# One tap = 5 flashes (~3.5 s). A lane change still waiting or moving after that gets another
# tap, at most MAX_TAPS per lane change (about 13 s, longer than REMOTE_LANE_MAX_TIME).
TAP_REPEAT_S = 4.5
MAX_TAPS = 3
# The driver's own lamp seen this long after our last tap is the driver's, not ours.
OWN_LAMP_S = 5.0
COMMAND_REPEATS = 3

RESULT_TEXT = {"ok": "", "busy": "처리 중", "cooldown": "대기", "lever": "레버 사용 중",
               "lamp": "깜빡이 켜짐", "nobcan": "B-CAN 없음", "abort": "버스 오류", "notx": "송신 불가"}


def command_packet(seq, side):
  if side not in ("L", "R"):
    raise ValueError(side)
  return f"{COMMAND_MAGIC} {int(seq) & 0xffff} {side}".encode()


def parse_status(data):
  """ESP32 status packet as a dict, or None for anything else on the port."""
  try:
    status = json.loads(data.decode("utf-8", "strict"))
  except (UnicodeDecodeError, ValueError):
    return None
  if not isinstance(status, dict) or status.get("t") != "dhbl" or status.get("v") != 1:
    return None
  out = {}
  for key in ("rssi", "bcan", "lamp", "err", "seq"):
    value = status.get(key)
    out[key] = int(value) if isinstance(value, (int, float)) and math.isfinite(value) else 0
  res = status.get("res")
  out["res"] = res if isinstance(res, str) and len(res) <= 16 else ""
  return out


def link_state(status, status_age):
  if status is None or status_age is None or status_age > LINK_STALE_S:
    return LINK_NONE
  if status["err"]:
    return LINK_ERROR
  return LINK_OK if status["bcan"] else LINK_NO_BCAN


class LaneTapPlanner:
  """Decides when a lane change asks the ESP32 for a tap ('L' / 'R' / None)."""

  def __init__(self):
    self.direction = None
    self.taps = 0
    self.last_tap = -1e9
    self.driver_owned = False

  def update(self, now, changing, direction, left_lamp, right_lamp):
    """changing: openpilot lateral active and laneChangeState pre/starting.
    direction: 'L', 'R' or None. *_lamp: carState blinker (lamp) bits."""
    if not changing or direction not in ("L", "R"):
      self.direction = None
      return None
    if direction != self.direction:
      self.direction = direction
      self.taps = 0
      lamp = left_lamp if direction == "L" else right_lamp
      # Lamp already on and not from our recent tap: the driver's lever started this change.
      self.driver_owned = bool(lamp) and now - self.last_tap > OWN_LAMP_S
    if self.driver_owned or self.taps >= MAX_TAPS:
      return None
    if self.taps and now - self.last_tap < TAP_REPEAT_S:
      return None
    self.taps += 1
    self.last_tap = now
    return direction


def write_status(path, payload):
  temporary = path + ".tmp"
  with open(temporary, "w") as f:
    json.dump(payload, f)
  os.replace(temporary, path)


def hud_status(status_file=STATUS_FILE, clock=time.time):
  """Status for the S9 HUD packet ("blinker"), or None when the feature is off or stalled."""
  try:
    with open(status_file) as f:
      status = json.load(f)
  except (OSError, ValueError):
    return None
  if not isinstance(status, dict):
    return None
  ts = status.get("wall")
  now = clock()
  if not isinstance(ts, (int, float)) or not math.isfinite(ts) or not 0 <= now - ts <= STATUS_STALE_S:
    return None
  out = {"link": int(status.get("link", LINK_NONE)), "rssi": int(status.get("rssi", 0))}
  event = status.get("event")
  if isinstance(event, dict) and isinstance(event.get("wall"), (int, float)):
    age = now - event["wall"]
    if 0 <= age <= EVENT_SHOW_S:
      out.update({"seq": int(event.get("seq", 0)), "dir": str(event.get("dir", "")),
                  "ok": bool(event.get("ok")), "why": str(event.get("why", "")), "age": int(age * 1000)})
  return out
