"""S9 key remote -> authenticated, short-lived EON button requests.

Adapted from carrot-wip bluetooth/model.py policies (962d4484): explicit
mapping, monotonic expiry, bounded journals, startup/replay rejection.
Android owns Bluetooth on S9; BlueZ/evdev is not transplanted onto EON.
"""
from collections import deque
import hashlib
import hmac
import json
import math
import os
import time
import uuid

COMMAND_FILE = "/dev/shm/hud_remote_cmd.json"
LANE_FILE = "/dev/shm/hud_remote_lane.json"
COMMAND_MAX_AGE_S = 0.4
LANE_CHANGE_HOLD_S = 0.3
POLL_FRAMES = 5
BUTTON_COMMANDS = {"res": "accelCruise", "set": "decelCruise",
                   "cancel": "cancel", "gap": "gapAdjustCruise"}
# 핸들 버튼을 길게 누른 것과 같은 동작(carrot-wip accelCruiseLong 등, 2026-10-18).
# RES/SET 길게 = 크루즈 속도 10단위, GAP 길게 = S9 HUD 정보 박스 전환.
LONG_BUTTON_COMMANDS = {"res_long": "accelCruise", "set_long": "decelCruise",
                        "gap_long": "gapAdjustCruise"}
PARAM_COMMANDS = ("nav_toggle", "nav_tmap", "nav_naver")
LANE_COMMANDS = {"lane_left": -1, "lane_right": 1}
ALL_COMMANDS = tuple(BUTTON_COMMANDS) + tuple(LONG_BUTTON_COMMANDS) + PARAM_COMMANDS + tuple(LANE_COMMANDS)
# 길게 누름 유지 프레임(100Hz). RES/SET 은 CruiseButtonLongDelay 를 한 번만 넘기고,
# GAP 은 cruise_helper.NAV_TOGGLE_HOLD_FRAMES(150) 를 넘긴다.
LONG_DELAY_DEFAULT = 40
GAP_LONG_HOLD_FRAMES = 152


def valid_hex(value, length=32):
  return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


# 연결 키: 8자리(2026-10-18 요청, 입력 편의). 이전에 받은 32자리 키도 계속 받는다.
KEY_LENGTHS = (8, 32)


def valid_key(value):
  return any(valid_hex(value, n) for n in KEY_LENGTHS)


def atomic_json(path, payload):
  temporary = path + ".tmp"
  with open(temporary, "w") as f:
    json.dump(payload, f)
  os.replace(temporary, path)


def fresh(created, started, now):
  return (isinstance(created, (float, int)) and math.isfinite(created) and
          started <= created <= now and now - created <= COMMAND_MAX_AGE_S)


class RemoteCommandSync:
  def __init__(self, params, command_file=COMMAND_FILE, lane_file=LANE_FILE, clock=time.monotonic):
    self.params, self.command_file, self.lane_file = params, command_file, lane_file
    self.clock = clock
    self.session = uuid.uuid4().hex
    self.ack = self.result = self.last_command = ""
    self.applied = {}
    self.events = []
    self.tickets = {}
    self.key = ""
    self.last_command_at = None

  def refresh_key(self):
    raw = self.params.get("HudRemoteKey") or b""
    key = raw.decode("ascii", errors="ignore") if isinstance(raw, bytes) else raw
    key = key if valid_key(key) else ""
    if key != self.key:
      self.key = key
      self.session = uuid.uuid4().hex
      self.tickets.clear()
      self.applied.clear()
      self.events.clear()
      self.ack = self.result = ""
    return key

  def receive(self, data, address, allowed=()):
    if address[1] != 7210 or len(data) > 256 or not self.refresh_key():
      return False
    try:
      body, signature = data.decode("ascii").rsplit(" ", 1)
      kind, session, request, ticket, command = body.split(" ")
    except (UnicodeDecodeError, ValueError):
      return False
    if (kind != "HUDCMD2" or session != self.session or command not in ALL_COMMANDS or
        not valid_hex(request) or not valid_hex(signature, 64)):
      return False
    digest = hmac.new(self.key.encode("ascii"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, digest):
      return False
    now = self.clock()
    created = self.tickets.get(ticket)
    if created is None or not fresh(created, 0.0, now):
      return False
    if request in self.applied:
      old_command, result = self.applied[request]
      if old_command != command:
        return False
    else:
      result = "blocked"
      if command in allowed:
        if not self._apply(command, request, now):
          return False
        result = "accepted"
      if len(self.applied) >= 128:
        del self.applied[next(iter(self.applied))]
      self.applied[request] = (command, result)
    self.ack, self.result = request, result
    return True

  def _apply(self, command, request, now):
    try:
      if command in BUTTON_COMMANDS or command in LONG_BUTTON_COMMANDS:
        events = [e for e in self.events if fresh(e["ts"], 0.0, now)]
        events.append({"id": self.session + request, "cmd": command, "ts": now})
        atomic_json(self.command_file, {"events": events[-32:]})
        self.events = events[-32:]
      elif command in LANE_COMMANDS:
        atomic_json(self.lane_file, {"direction": LANE_COMMANDS[command], "ts": now})
      else:
        try:
          current = int(self.params.get("EonClusterHudNavApp") or 1)
        except (ValueError, TypeError):
          current = 1
        target = {"nav_tmap": 1, "nav_naver": 2}.get(command, 2 if current == 1 else 1)
        self.params.put("EonClusterHudNavApp", str(target))
    except OSError:
      return False
    self.last_command, self.last_command_at = command, now
    return True

  def telemetry(self):
    self.refresh_key()
    now = self.clock()
    self.tickets = {t: ts for t, ts in self.tickets.items() if fresh(ts, 0.0, now)}
    ticket = uuid.uuid4().hex[:16] if self.key else ""
    if ticket:
      self.tickets[ticket] = now
    return {"hudCmdSession": self.session, "hudCmdTicket": ticket,
            "hudCmdAck": self.ack, "hudCmdResult": self.result,
            "hudCmdLast": self.last_command,
            "hudCmdAgeMs": int((now - self.last_command_at) * 1000) if self.last_command_at is not None else -1}


class RemoteButtonSource:
  def __init__(self, command_file=COMMAND_FILE, clock=time.monotonic, long_delay=None):
    self.command_file, self.clock = command_file, clock
    self.started = clock()
    self.frame = 0
    self.seen = deque(maxlen=128)
    self.release_type = None
    self.hold_type = None
    self.hold_frames = 0
    self.long_delay = long_delay

  def _long_delay(self):
    if self.long_delay is not None:
      return self.long_delay
    try:
      from common.params import Params
      return min(max(int(Params().get("CruiseButtonLongDelay") or LONG_DELAY_DEFAULT), 30), 150)
    except Exception:
      return LONG_DELAY_DEFAULT

  def poll(self, allowed=True):
    if self.hold_type is not None:
      # 길게 누름: 유지 시간을 채우면 뗀다. 운전자 버튼 조작 등으로 허용이 끊기면 바로 뗀다.
      self.hold_frames -= 1
      if self.hold_frames > 0 and allowed:
        return []
      button, self.hold_type = self.hold_type, None
      return [(button, False)]
    if self.release_type is not None:
      button, self.release_type = self.release_type, None
      return [(button, False)]
    self.frame += 1
    if self.frame % POLL_FRAMES:
      return []
    try:
      with open(self.command_file) as f:
        payload = json.load(f)
      events = payload.get("events", [])
      if not isinstance(events, list):
        return []
    except (OSError, ValueError, AttributeError):
      return []
    now = self.clock()
    for event in events[-32:]:
      if not isinstance(event, dict):
        continue
      request = event.get("id")
      if not isinstance(request, str) or request in self.seen:
        continue
      self.seen.append(request)
      command = event.get("cmd")
      if command not in BUTTON_COMMANDS and command not in LONG_BUTTON_COMMANDS:
        continue
      if not fresh(event.get("ts"), self.started, now):
        continue
      if not allowed and command != "cancel":
        continue
      if command in LONG_BUTTON_COMMANDS:
        self.hold_type = LONG_BUTTON_COMMANDS[command]
        self.hold_frames = GAP_LONG_HOLD_FRAMES if command == "gap_long" else self._long_delay() + 2
        return [(self.hold_type, True)]
      self.release_type = BUTTON_COMMANDS[command]
      return [(self.release_type, True)]
    return []

  def button_events(self, allowed=True):
    from cereal import car
    out = []
    for button, pressed in self.poll(allowed):
      event = car.CarState.ButtonEvent.new_message()
      event.type, event.pressed = button, pressed
      out.append(event)
    return out


class RemoteLaneChangeSource:
  def __init__(self, lane_file=LANE_FILE, clock=time.monotonic):
    self.lane_file, self.clock = lane_file, clock
    self.started = clock()

  def poll(self):
    now = self.clock()
    try:
      with open(self.lane_file) as f:
        payload = json.load(f)
      ts = payload.get("ts")
      direction = payload.get("direction")
      if fresh(ts, self.started, now) and now - ts <= LANE_CHANGE_HOLD_S and direction in (-1, 1):
        return direction
    except (OSError, ValueError, AttributeError, TypeError):
      pass
    return 0


def allowed_commands(started, car_valid, drive, brake, gas):
  allowed = set(PARAM_COMMANDS)
  if started and car_valid:
    # carrot-wip 처럼 크루즈 버튼은 시동·차량 데이터만 보고 받는다. 기어·페달에 따른
    # 실제 동작 여부는 핸들 버튼과 똑같이 크루즈 로직이 판단한다(2026-10-18).
    allowed.update(BUTTON_COMMANDS)
    allowed.update(LONG_BUTTON_COMMANDS)
    if drive and not brake and not gas:
      allowed.update(LANE_COMMANDS)
  return allowed
