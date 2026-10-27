"""EON Bluetooth remote (bt_remote) -> short-lived local requests for carState and lateral planning.

bt_remote reads the remote's kernel input node on EON itself; nothing goes over
Wi-Fi or through a phone. Accepted commands are journaled in /dev/shm and
expire after COMMAND_MAX_AGE_S, so a stalled consumer never applies an old press.
"""
from collections import deque
import json
import math
import os
import time
import uuid

COMMAND_FILE = "/dev/shm/hud_remote_cmd.json"
LANE_FILE = "/dev/shm/hud_remote_lane.json"
STATUS_FILE = "/dev/shm/hud_remote_status.json"
COMMAND_MAX_AGE_S = 0.4
LANE_CHANGE_HOLD_S = 0.3
POLL_FRAMES = 5
BUTTON_COMMANDS = {"res": "accelCruise", "set": "decelCruise",
                   "cancel": "cancel", "gap": "gapAdjustCruise"}
# 핸들 버튼을 길게 누른 것과 같은 동작(carrot-wip accelCruiseLong 등, 2026-10-18).
# RES/SET 길게 = 크루즈 속도 10단위. 티맵↔네이버는 nav_toggle 이 직접 바꾼다.
LONG_BUTTON_COMMANDS = {"res_long": "accelCruise", "set_long": "decelCruise"}
PARAM_COMMANDS = ("nav_toggle", "nav_tmap", "nav_naver", "hud_box")
LANE_COMMANDS = {"lane_left": -1, "lane_right": 1}
ALL_COMMANDS = tuple(BUTTON_COMMANDS) + tuple(LONG_BUTTON_COMMANDS) + PARAM_COMMANDS + tuple(LANE_COMMANDS)
# 길게 누름 유지 프레임(100Hz). RES/SET 은 CruiseButtonLongDelay 를 한 번만 넘긴다.
LONG_DELAY_DEFAULT = 40

# HUD 리모컨 표시. link: 0 연결 안 됨, 1 절전(오래 안 눌러 끊김), 2 연결됨, 3 비정상 끊김.
LINK_NONE, LINK_SLEEP, LINK_OK, LINK_LOST = 0, 1, 2, 3
STATUS_STALE_S = 3.0
EVENT_SHOW_S = 1.5
COMMAND_LABELS = {"res": "RES", "set": "SET", "gap": "GAP", "cancel": "취소",
                  "res_long": "RES 길게", "set_long": "SET 길게",
                  "nav_toggle": "맵 전환", "nav_tmap": "티맵", "nav_naver": "네이버",
                  "hud_box": "정보 박스", "lane_left": "차선 좌", "lane_right": "차선 우"}


def atomic_json(path, payload):
  temporary = path + ".tmp"
  with open(temporary, "w") as f:
    json.dump(payload, f)
  os.replace(temporary, path)


def read_json(path):
  try:
    with open(path) as f:
      return json.load(f)
  except (OSError, ValueError):
    return None


def fresh(created, started, now, max_age=COMMAND_MAX_AGE_S):
  return (isinstance(created, (float, int)) and math.isfinite(created) and
          started <= created <= now and now - created <= max_age)


class RemoteCommandSync:
  """Journals accepted commands for carState (buttons) and DesireHelper (lane changes)."""

  def __init__(self, params, command_file=COMMAND_FILE, lane_file=LANE_FILE, clock=time.monotonic):
    self.params, self.command_file, self.lane_file = params, command_file, lane_file
    self.clock = clock
    self.session = uuid.uuid4().hex
    self.events = []
    self.vehicle = (False, False, False, False, False)
    self.seq = 0
    self.last_event = None

  def apply(self, command, allowed):
    """Apply one command if the car allows it; returns 'accepted', 'blocked' or 'error'."""
    if command not in ALL_COMMANDS:
      return "blocked"
    now = self.clock()
    result = "blocked"
    if command in allowed:
      result = "accepted" if self._apply(command, uuid.uuid4().hex, now) else "error"
    self.seq += 1
    why = block_reason(command, *self.vehicle) if result == "blocked" else ("처리 실패" if result == "error" else "")
    self.last_event = (self.seq, command, result, why, now)
    return result

  def event_status(self, now=None):
    """The last command for the HUD, while it is still worth showing."""
    now = self.clock() if now is None else now
    event = self.last_event
    if event is None or not 0.0 <= now - event[4] <= EVENT_SHOW_S:
      return {}
    seq, command, result, why, ts = event
    return {"seq": seq, "cmd": COMMAND_LABELS.get(command, command),
            "ok": result == "accepted", "why": why, "age": int((now - ts) * 1000)}

  def _apply(self, command, request, now):
    try:
      if command in BUTTON_COMMANDS or command in LONG_BUTTON_COMMANDS:
        events = [e for e in self.events if fresh(e["ts"], 0.0, now)]
        events.append({"id": self.session + request, "cmd": command, "ts": now})
        atomic_json(self.command_file, {"events": events[-32:]})
        self.events = events[-32:]
      elif command in LANE_COMMANDS:
        atomic_json(self.lane_file, {"direction": LANE_COMMANDS[command], "ts": now})
      elif command == "hud_box":
        # HUD info box: 1 = HUD info, 2 = trip summary.
        try:
          box = int(self.params.get("EonClusterHudInfoBox") or 1)
        except (ValueError, TypeError):
          box = 1
        self.params.put("EonClusterHudInfoBox", "1" if box == 2 else "2")
      else:
        try:
          current = int(self.params.get("EonClusterHudNavApp") or 1)
        except (ValueError, TypeError):
          current = 1
        target = {"nav_tmap": 1, "nav_naver": 2}.get(command, 2 if current == 1 else 1)
        self.params.put("EonClusterHudNavApp", str(target))
    except OSError:
      return False
    return True


def hud_status(status_file=STATUS_FILE, clock=time.monotonic):
  """bt_remote status for the S9 HUD packet, or None when the remote is off or bt_remote stalled."""
  status = read_json(status_file)
  if not isinstance(status, dict) or not fresh(status.get("ts"), 0.0, clock(), STATUS_STALE_S):
    return None
  out = {"link": status.get("link", LINK_NONE)}
  event = status.get("event")
  if isinstance(event, dict) and event:
    out.update(event)
  return out


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
    payload = read_json(self.command_file)
    events = payload.get("events", []) if isinstance(payload, dict) else []
    if not isinstance(events, list):
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
        self.hold_frames = self._long_delay() + 2
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
    payload = read_json(self.lane_file)
    if not isinstance(payload, dict):
      return 0
    ts = payload.get("ts")
    direction = payload.get("direction")
    if fresh(ts, self.started, now) and now - ts <= LANE_CHANGE_HOLD_S and direction in (-1, 1):
      return direction
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
  return frozenset(allowed)


def block_reason(command, started, car_valid, drive, brake, gas):
  """allowed_commands 가 이 명령을 막은 이유(HUD 표시용)."""
  if command in PARAM_COMMANDS:
    return ""
  if not started:
    return "시동 꺼짐"
  if not car_valid:
    return "차량 데이터 없음"
  if command in LANE_COMMANDS:
    if not drive:
      return "D단 아님"
    if brake:
      return "브레이크"
    if gas:
      return "가속페달"
  return ""
