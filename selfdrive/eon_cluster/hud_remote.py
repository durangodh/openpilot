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
PARAM_COMMANDS = ("nav_toggle", "nav_tmap", "nav_naver")
LANE_COMMANDS = {"lane_left": -1, "lane_right": 1}
ALL_COMMANDS = tuple(BUTTON_COMMANDS) + PARAM_COMMANDS + tuple(LANE_COMMANDS)


def valid_hex(value, length=32):
  return isinstance(value, str) and len(value) == length and all(c in "0123456789abcdef" for c in value)


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
    key = key if valid_hex(key) else ""
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
      if command in BUTTON_COMMANDS:
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
  def __init__(self, command_file=COMMAND_FILE, clock=time.monotonic):
    self.command_file, self.clock = command_file, clock
    self.started = clock()
    self.frame = 0
    self.seen = deque(maxlen=128)
    self.release_type = None

  def poll(self, allowed=True):
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
      if command not in BUTTON_COMMANDS or not fresh(event.get("ts"), self.started, now):
        continue
      if not allowed and command != "cancel":
        continue
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
    allowed.add("cancel")
    if drive and not brake and not gas:
      allowed.update(BUTTON_COMMANDS)
      allowed.update(LANE_COMMANDS)
  return allowed
