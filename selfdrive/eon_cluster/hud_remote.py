"""HUD remote control: Bluetooth remote → S9 Remote HUD app → EON.

Two halves live here so they share one protocol definition:

* ``RemoteCommandSync`` runs inside ``remote_hud`` (the 7210 telemetry loop).
  It accepts idempotent ``HUDCMD1 <session> <id> <cmd>`` requests from the
  HUD app, acknowledges them through telemetry (``hudCmdAck``) and hands
  each new command to the rest of the system: cruise button commands are
  written to ``/dev/shm/hud_remote_cmd.json``; the navigation-app toggle is
  applied straight to ``EonClusterHudNavApp``.

* ``RemoteButtonSource`` runs inside the car interface (100 Hz). It polls the
  command file cheaply (``os.stat`` every few frames) and turns a new command
  into a one-frame ``pressed`` ButtonEvent followed by the release, exactly
  like a steering-wheel button transition. The car interface appends those
  to ``ret.buttonEvents`` so controlsd, cruise_helper and the engage logic see
  a normal RES/SET/CANCEL/GAP press.

Safety: only button *presses* can be injected (never a steering or
acceleration command), one command at a time, and a command older than
``COMMAND_MAX_AGE_S`` is dropped so a stale file can never fire later.
"""
import json
import os
import time
import uuid

from cereal import car

ButtonType = car.CarState.ButtonEvent.Type

COMMAND_FILE = "/dev/shm/hud_remote_cmd.json"
COMMAND_MAX_AGE_S = 1.5
POLL_FRAMES = 5           # 100 Hz interface -> stat the file every 50 ms

# Remote command name -> ButtonEvent type. RES also raises the set speed and
# SET lowers it on Hyundai, so the remote's "speed ±" keys map to these two.
BUTTON_COMMANDS = {
  "res": ButtonType.accelCruise,
  "set": ButtonType.decelCruise,
  "cancel": ButtonType.cancel,
  "gap": ButtonType.gapAdjustCruise,
}
# Commands the EON applies itself (no car button involved).
PARAM_COMMANDS = ("nav_toggle", "nav_tmap", "nav_naver")
# Lane change request (carrot "LANECHANGE LEFT/RIGHT"): a virtual blinker held for
# LANE_CHANGE_HOLD_S per command. The sender repeats the command while the key is
# held, so releasing the key ends the request. desire_helper applies the same
# gates as NOO (LaneChangeNeedTorque, speed, road edge, BSD, opposite torque).
LANE_COMMANDS = {"lane_left": -1, "lane_right": 1}
LANE_CHANGE_HOLD_S = 0.3
LANE_FILE = "/dev/shm/hud_remote_lane.json"
ALL_COMMANDS = tuple(BUTTON_COMMANDS) + PARAM_COMMANDS + tuple(LANE_COMMANDS)


class RemoteCommandSync:
  """Request/ack handling for HUDCMD1 on the EON reply socket (remote_hud)."""

  def __init__(self, params, command_file=COMMAND_FILE, lane_file=LANE_FILE):
    self.params = params
    self.command_file = command_file
    self.lane_file = lane_file
    self.session = uuid.uuid4().hex
    self.ack = ""
    self.applied = {}
    self.seq = 0
    self.last_command = ""
    self.last_command_at = 0.0

  def receive(self, data, address):
    if address[1] != 7210 or len(data) > 128:
      return False
    try:
      kind, session, request, command = data.decode("ascii").split(" ")
    except (UnicodeDecodeError, ValueError):
      return False
    if (kind != "HUDCMD1" or session != self.session or command not in ALL_COMMANDS or
        len(request) != 32 or any(c not in "0123456789abcdef" for c in request)):
      return False
    if request not in self.applied:
      if not self._apply(command):
        return False
      if len(self.applied) >= 32:
        del self.applied[next(iter(self.applied))]
      self.applied[request] = command
    self.ack = request
    return True

  def _apply(self, command):
    now = time.time()
    if command in BUTTON_COMMANDS:
      self.seq += 1
      payload = {"seq": self.seq, "cmd": command, "ts": now}
      tmp = self.command_file + ".tmp"
      try:
        with open(tmp, "w") as f:
          json.dump(payload, f)
        os.rename(tmp, self.command_file)
      except OSError:
        return False
    elif command in LANE_COMMANDS:
      payload = {"direction": LANE_COMMANDS[command], "ts": now}
      tmp = self.lane_file + ".tmp"
      try:
        with open(tmp, "w") as f:
          json.dump(payload, f)
        os.rename(tmp, self.lane_file)
      except OSError:
        return False
    elif command in PARAM_COMMANDS:
      try:
        current = 2 if int(self.params.get("EonClusterHudNavApp") or 1) == 2 else 1
      except (TypeError, ValueError):
        current = 1
      target = {"nav_tmap": 1, "nav_naver": 2}.get(command, 2 if current == 1 else 1)
      try:
        self.params.put("EonClusterHudNavApp", str(target))
      except Exception:
        return False
    self.last_command = command
    self.last_command_at = now
    return True

  def telemetry(self):
    return {"hudCmdSession": self.session, "hudCmdAck": self.ack,
            "hudCmdLast": self.last_command,
            "hudCmdAgeMs": int((time.time() - self.last_command_at) * 1000) if self.last_command_at else -1}


class RemoteButtonSource:
  """Turns commands from COMMAND_FILE into press/release ButtonEvents (car interface)."""

  def __init__(self, command_file=COMMAND_FILE):
    self.command_file = command_file
    self.frame = 0
    self.last_mtime = None
    self.last_seq = None
    self.release_type = None
    self.last_button = ""
    self.last_button_at = 0.0

  def _read(self):
    try:
      with open(self.command_file) as f:
        payload = json.load(f)
      seq = int(payload.get("seq"))
      cmd = payload.get("cmd")
      ts = float(payload.get("ts", 0.0))
    except (OSError, ValueError, TypeError, AttributeError):
      return None
    if cmd not in BUTTON_COMMANDS:
      return None
    if seq == self.last_seq:
      return None
    self.last_seq = seq
    if time.time() - ts > COMMAND_MAX_AGE_S:
      return None   # stale (EON rebooted, file left over): never fire late
    return cmd

  def poll(self):
    """Returns a list of (ButtonType, pressed) to append this frame."""
    events = []
    if self.release_type is not None:
      events.append((self.release_type, False))
      self.release_type = None
      return events
    self.frame += 1
    if self.frame % POLL_FRAMES:
      return events
    try:
      mtime = os.stat(self.command_file).st_mtime
    except OSError:
      return events
    if mtime == self.last_mtime:
      return events
    self.last_mtime = mtime
    cmd = self._read()
    if cmd is None:
      return events
    button = BUTTON_COMMANDS[cmd]
    self.release_type = button
    self.last_button = cmd
    self.last_button_at = time.time()
    events.append((button, True))
    return events

  def button_events(self):
    """Same as poll() but as car.CarState.ButtonEvent messages."""
    out = []
    for button, pressed in self.poll():
      be = car.CarState.ButtonEvent.new_message()
      be.type = button
      be.pressed = pressed
      out.append(be)
    return out


class RemoteLaneChangeSource:
  """desire_helper (20 Hz): -1/0/1 while a remote lane request is being held."""

  def __init__(self, lane_file=LANE_FILE):
    self.lane_file = lane_file
    self.frame = 0
    self.last_mtime = None
    self.direction = 0
    self.until = 0.0

  def poll(self):
    self.frame += 1
    now = time.time()
    if self.frame % 2 == 0:
      try:
        mtime = os.stat(self.lane_file).st_mtime
      except OSError:
        mtime = None
      if mtime is not None and mtime != self.last_mtime:
        self.last_mtime = mtime
        try:
          with open(self.lane_file) as f:
            payload = json.load(f)
          direction = int(payload.get("direction", 0))
          ts = float(payload.get("ts", 0.0))
        except (OSError, ValueError, TypeError, AttributeError):
          direction, ts = 0, 0.0
        if direction in (-1, 1) and now - ts <= COMMAND_MAX_AGE_S:
          self.direction = direction
          self.until = ts + LANE_CHANGE_HOLD_S
    if now > self.until:
      self.direction = 0
    return self.direction
