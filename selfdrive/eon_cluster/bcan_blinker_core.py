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
# The ESP32 broadcasts every second, but the phone hotspot drops broadcasts now and then
# (2026-11-15 road log: 3 s gaps every few seconds while driving), so allow 6 s.
LINK_STALE_S = 6.0
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
               "lamp": "깜빡이 켜짐", "nobcan": "B-CAN 없음", "abort": "버스 오류", "notx": "송신 불가",
               "nothing": "끌 깜빡이 없음", "nolamp": "이미 꺼짐", "nohold": "유지 끝남"}


def command_packet(seq, side):
  if side not in ("L", "R", "C", "HL", "HR", "KL", "KR"):
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
  hold = status.get("hold")
  out["hold"] = int(hold) if isinstance(hold, (int, float)) and math.isfinite(hold) else None  # None: old firmware
  for key in ("kc", "rx", "hf"):
    value = status.get(key)
    out[key] = int(value) if isinstance(value, (int, float)) and math.isfinite(value) else None
  lc = status.get("lc")
  out["lc"] = lc if isinstance(lc, str) and len(lc) <= 16 else ""
  hend = status.get("hend")
  out["hend"] = hend if isinstance(hend, str) and len(hend) <= 16 else ""
  return out


def lane_direction(value):
  """lateralPlan.laneChangeDirection (capnp enum, its name, or None) -> 'L' / 'R' / None.
  A capnp enum read from a message does not hash like the schema constants, so a dict keyed
  by LaneChangeDirection.left never matched (2026-11-15 road log: dir None, no tap)."""
  return {"left": "L", "right": "R"}.get(str(value))


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
    self.external_side = None

  def note_external_tap(self, now, side):
    """The remote press already tapped this side: it counts as the lane change's first tap."""
    self.last_tap = now
    self.external_side = side

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
      if self.external_side == direction and now - self.last_tap < TAP_REPEAT_S:
        self.taps = 1   # the remote press tapped already; repeat only for a long lane change
      self.external_side = None
    if self.driver_owned or self.taps >= MAX_TAPS:
      return None
    if self.taps and now - self.last_tap < TAP_REPEAT_S:
      return None
    self.taps += 1
    self.last_tap = now
    return direction


# Cancel timing. The model's laneChangeFinishing comes 4-5 s after laneChangeStarting (2026-11-15
# road logs), after the 5 flashes (~3.5 s) are over, so the lane line crossing is the trigger:
# the near line on the change side closes in on ego and, once ego is over it, the model's near line
# becomes the next lane's, a jump of about a lane width. Without lane lines, CANCEL_FALLBACK_S after
# laneChangeStarting.
CANCEL_FALLBACK_S = 2.0
CROSS_NEAR_M = 1.0       # the change-side line was this close to ego ...
CROSS_JUMP_M = 1.5       # ... then moved this much further away in one step
CROSS_MIN_PROB = 0.3


class LaneCrossDetector:
  """True once when ego crosses the lane line on the change side (modelV2 laneLines 1 left / 2 right).
  Sign-free: uses the distance |y| of that line at the car, so it does not depend on the y axis."""

  def __init__(self):
    self.prev = None

  def reset(self):
    self.prev = None

  def update(self, near_abs_y, prob):
    if near_abs_y is None or prob is None or prob < CROSS_MIN_PROB or not math.isfinite(near_abs_y):
      self.prev = None
      return False
    crossed = self.prev is not None and self.prev < CROSS_NEAR_M and near_abs_y - self.prev > CROSS_JUMP_M
    self.prev = near_abs_y
    return crossed


class BlinkCancelPlanner:
  """Ends our own 5-flash one-touch once the car is in the new lane (DhBcanBlinkerCancel).

  The tap gives 5 flashes (~3.5 s) but a single lane change is over after 2-3, and the extra flashes
  look like another lane change to the car behind. After a lane change we tapped for crosses the
  line (or CANCEL_FALLBACK_S into laneChangeStarting, or at laneChangeFinishing), ask the ESP32 once
  for a cancel ('C': a short tap the other way)."""

  def __init__(self):
    self.tapped = False
    self.tapped_time = None
    self.start = None

  def update(self, now, state, tapped_side, crossed=False):
    """state: laneChangeState name; tapped_side: 'L'/'R' when a tap was just sent; crossed: the lane
    line crossing was just seen. True = cancel now."""
    if tapped_side in ("L", "R"):
      self.tapped = True
      self.tapped_time = now
      self.start = None
    # A remote press taps before the lane change leaves "off"; keep it a moment.
    if state == "off" and self.tapped and self.tapped_time is not None and now - self.tapped_time <= TAP_REPEAT_S:
      return False
    if state == "off" or not self.tapped:
      self.tapped = False
      self.start = None
      return False
    if state == "laneChangeStarting" and self.start is None:
      self.start = now
    due = state == "laneChangeFinishing" or \
      (self.start is not None and (crossed or now - self.start >= CANCEL_FALLBACK_S))
    if due:
      self.tapped = False
      self.start = None
    return due


# Remote lane button (hud_remote BLINK_FILE), like the lever at any speed: one-touch on that side,
# or end our own one-touch if it is still blinking and no lane change is running (second press).
STOPPED_BLINK_FILE = "/dev/shm/hud_remote_blink.json"
BLINK_REQUEST_MAX_AGE_S = 1.0
OWN_BLINK_S = 3.6        # one-touch: 5 flashes in about 3.5 s


class BlinkRequestSource:
  """New stopped-blinker requests from the remote ('L' / 'R' / None), each id once."""

  def __init__(self, path=STOPPED_BLINK_FILE, clock=time.monotonic):
    self.path, self.clock = path, clock
    self.last_id = None
    self.started = clock()

  def poll(self):
    try:
      with open(self.path) as f:
        payload = json.load(f)
    except (OSError, ValueError):
      return None
    if not isinstance(payload, dict):
      return None
    rid, ts, direction = payload.get("id"), payload.get("ts"), payload.get("direction")
    now = self.clock()
    if rid == self.last_id or not isinstance(ts, (int, float)) or not math.isfinite(ts):
      return None
    self.last_id = rid
    if not (self.started <= ts <= now and now - ts <= BLINK_REQUEST_MAX_AGE_S):
      return None
    return {-1: "L", 1: "R"}.get(direction)


def stopped_blink_action(request, own_tap_time, now, lane_change_active=False):
  """Remote press -> 'L'/'R' to tap, 'C' to end our own one-touch still blinking, or None.
  During a lane change a press is the lane change's business (DesireHelper), not a cancel."""
  if request not in ("L", "R"):
    return None
  if own_tap_time is not None and 0.0 <= now - own_tap_time <= OWN_BLINK_S:
    return None if lane_change_active else "C"
  return request


# Hold mode (DhBcanBlinkerHold): a press keeps that side blinking (ESP32 HL/HR, firmware
# 2026-11-16) until the other button; EON repeats it every HOLD_KEEPALIVE_S as a keepalive.
HOLD_KEEPALIVE_S = 1.0
HOLD_MAX_S = 180.0


class NooHoldPlanner:
  """Keep an automatic navigation signal through a long turn-pocket wait.

  Uses only the existing ESP32 hold protocol. A remote press owns priority;
  an ended/rejected hold is not restarted until the navigation request clears.
  Never cancels a remote/driver-owned hold.
  """

  def __init__(self):
    self.side = None
    self.blocked = False

  def update(self, desired, hold_side, allowed, remote_request=None, lamps_on=False):
    if remote_request is not None:
      self.side = None
      self.blocked = desired is not None
      return None
    if desired is None:
      self.blocked = False
    if self.side is not None:
      if hold_side != self.side:
        self.side = None
        self.blocked = desired is not None
      elif not allowed or desired != self.side:
        self.side = None
        self.blocked = desired is not None
        return "C"
      return None
    if allowed and desired in ("L", "R") and not self.blocked and hold_side is None and not lamps_on:
      self.side = desired
      return "H" + desired
    return None


def hold_press_action(request, hold_side, lit=None):
  """Remote press in hold mode -> 'HL' / 'HR' to start holding, 'C' to stop, or None.
  lit: the side whose lamp is flashing now ('L' / 'R' / None). The other button while one side
  flashes always stops it, also when the EON lost track of the hold (a failed first tap answered
  'abort' while the ESP32 kept holding: the first press started the other side, 2026-11-19)."""
  if request not in ("L", "R"):
    return None
  if hold_side is None:
    return "C" if lit in ("L", "R") and lit != request else "H" + request
  return "C" if request != hold_side else None


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
