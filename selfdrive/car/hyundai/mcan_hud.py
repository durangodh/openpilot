"""Naver turn-by-turn on the Genesis DH stock HUD over MM-CAN (panda bus 1).

The head unit (AVN) feeds the HUD and cluster with (opendbc hyundai_2015_mcan.dbc,
checked against our 2026-10-31 M-CAN logs):
  0x115 HU_CLU_PE_02  [display type, side street(2), direction, distance m(2), 0, bar<<4]
                      direction = clockwise turn angle / 7.5 deg (right 12, left 36)
                      display type: 13 turn, 31 U-turn, 70 keep going, 112 destination
                      ahead, 3 arrived, 8 no guidance (idle, sent at 5 Hz all the time)
  0x123 HU_CLU_PE_07  byte 7 = distance bar in percent
  0x4A9 (ISO-TP)      HUD text: 0xF0 + UTF-16LE (direction/road name); HUD answers 0x4B9

The AVN keeps sending its idle 0x115/0x123 even without a destination. Sending ours
at 50 Hz alone still flickered on the car (2026-10-31), so the panda keeps our last
frame of each and sends it again right after every AVN frame (safety_hyundai_community.h);
we only need to keep it fresh, at 20 Hz.
Sending requires the panda's bus 1 to be live (McanHudTbt, through an MM-CAN
low-speed/fault-tolerant <-> high-speed converter); the panda only lets these three
IDs out on bus 1.
"""
from selfdrive.car import make_can_msg
from selfdrive.controls.lib.navigation_route import NavigationRouteData

MCAN_BUS = 1
# panda safety param bit that makes bus 1 live (board/safety/safety_defaults.h)
MCAN_TX_PARAM = 0x0400
TBT_ADDR = 0x115
BAR_ADDR = 0x123
TEXT_ADDR = 0x4A9
CAMERA_ADDR = 0x1E5

# 0x1E5 HU_CLU_PE_11 speed camera, per camera kind a nibble of limit / 10 (2026-11-01 07:14
# drive): byte 0 low = fixed camera (04 = the 40 circle in the 07:17 photo, 0a = 100),
# byte 0 high = mobile, byte 1 high = signal+speed (00 50 = 50); byte 1 bits 2-3 =
# over the limit. The rest of the frame is what the AVN sent with a camera.
# When the AVN shows the camera: 100 km/h highway cameras from about 700 m (2026-11-01
# 07:46), city cameras from 400 m (a 50 km/h camera, confirmed by the driver 11-01).
CAMERA_SHOW_M_FAST = 700.0      # limit >= 80 km/h
CAMERA_SHOW_M_SLOW = 400.0
SDI_SIGNAL_SPEED = 0       # navi module SDI types (NaverCodes / TMAP)
SDI_SPEED = 1
SDI_MOBILE = 7

DISPLAY_TURN = 13
DISPLAY_UTURN = 31
DISPLAY_DEST_AHEAD = 112
DISPLAY_ARRIVED = 3
DISPLAY_TOLLGATE = 178     # 0xB2 tollgate icon (2026-11-01 07:18 북오산톨게이트), uses the bar
DISPLAY_HIGHWAY_EXIT = 164 # 0xA4 highway EXIT sign preview, km distance (11-01 07:42 "1.7km")
HIGHWAY_EXIT_PREVIEW_M = 1000  # the AVN switches to the "r" exit arrow from ~900 m
# Keep going: 0x40 + a preview of the next maneuver. 65 plain straight (07:19 "12.6km"),
# 66 bear right (07:15 before the 북오산IC exit), 67 right, 70 left, 72 U-turn, as the
# AVN sent them before each kind of maneuver in the 10-31/11-01 logs.
DISPLAY_STRAIGHT = 65
STRAIGHT_PREVIEW = {13: 67, 19: 67, 18: 66, 21: 66, 12: 70, 16: 70, 17: 70, 20: 70, 14: 72}
DIRECTION_NONE = 15        # the AVN's direction byte on keep-going / reroute frames
# Off route / rerouting: the AVN's frame while its HUD shows "경로 탐색 중입니다".
REROUTE_PAYLOAD = bytes.fromhex("0900000f000500f0")
SIDE_TURN = 0x0811         # the AVN's most common intersection-shape value for turns
SIDE_EXIT = 0x0003         # the AVN's exit/ramp shape ("r", 북오산IC 07:17, dir 3)
BAR_NONE = 0xF
ARRIVED_DIST_M = 15
SEND_PERIOD_FRAMES = 5     # 20 Hz from the 100 Hz carcontroller (panda echo freshness 300 ms)

# turn_type (TMAP codes; the Naver and Kakao modules map theirs onto them)
#   -> (display, direction, intersection shape)
TURN_CODES = {
  12: (DISPLAY_TURN, 36, SIDE_TURN),    # left (9 o'clock)
  13: (DISPLAY_TURN, 12, SIDE_TURN),    # right (3 o'clock)
  14: (DISPLAY_UTURN, 36, 0),           # U-turn, as the AVN draws it (2026-10-31 08:39 drive)
  16: (DISPLAY_TURN, 32, SIDE_TURN),    # 8 o'clock
  17: (DISPLAY_TURN, 45, 0x4001),       # keep left / left fork (AVN 07:45:46 서오산분기점)
  18: (DISPLAY_TURN, 3, SIDE_EXIT),     # keep right / right exit, IC (AVN 0d 00 03 03 ..)
  19: (DISPLAY_TURN, 16, SIDE_TURN),    # 4 o'clock
  20: (DISPLAY_TURN, 44, SIDE_TURN),    # lane change left
  21: (DISPLAY_TURN, 4, SIDE_TURN),     # lane change right
}
TURN_STRAIGHT = 11
# Naver/Kakao modules map the destination to 2, TMAP sends its own 201.
TURN_ARRIVE = (2, 201)

# Fallback stock bytes 0-6 of 0x123 until the AVN's own frame has been seen.
BAR_PREFIX_DEFAULT = bytes.fromhex("000000c0ffff0f")


def bar_tenths(dist_m):
  """10-step bar of 0x115 byte 7, as the AVN does it: full beyond 300 m, 30 m per step."""
  if dist_m > 300:
    return 10
  return max(1, min(10, int(dist_m // 30)))


def bar_percent(dist_m):
  return max(1, min(100, int(round(dist_m / 3.0))))


def distance_fields(dist):
  """0x115 bytes 4-5 (value), byte 6 (unit) and the low nibble of byte 7 (tenths).
  Metres below 1 km. From 1 km the AVN sends whole km in bytes 4-5, 0x10 in byte 6
  and the tenths in byte 7's low nibble: 00 42 10 .7 was "66.7km" and 00 14 10 .0
  "20.0km" on the HUD (2026-10-31 / 2026-11-01 logs)."""
  if dist < 1000:
    return dist >> 8, dist & 0xFF, 0, 0
  km = min(0xFFFF, dist // 1000)
  return km >> 8, km & 0xFF, 0x10, (dist % 1000) // 100


def tbt_payload(turn_type, dist_m, text="", next_turn_type=-1):
  """0x115 payload for one Naver maneuver, or None to leave the AVN's frame alone.
  next_turn_type picks the keep-going preview icon; a 톨게이트 name on a straight
  maneuver shows the tollgate icon (the modules map tollgates to straight)."""
  dist = max(0, min(0xFFFFFF, int(round(dist_m))))
  hi, lo, unit, tenths = distance_fields(dist)
  if turn_type in TURN_ARRIVE:
    if dist <= ARRIVED_DIST_M:
      return bytes([DISPLAY_ARRIVED, 0, 0, 0, 0, 0, 0, 0xF0])
    return bytes([DISPLAY_DEST_AHEAD, 0, 0, 0, hi, lo, unit, (BAR_NONE << 4) | tenths])
  if turn_type == TURN_STRAIGHT:
    if "톨게이트" in (text or ""):
      bar = BAR_NONE if dist > 300 else bar_tenths(dist)   # AVN: no bar until 300 m
      return bytes([DISPLAY_TOLLGATE, 0, 0, DIRECTION_NONE, hi, lo, unit, (bar << 4) | tenths])
    display = STRAIGHT_PREVIEW.get(next_turn_type, DISPLAY_STRAIGHT)
    return bytes([display, 0, 0, DIRECTION_NONE, hi, lo, unit, (BAR_NONE << 4) | tenths])
  if turn_type == 18 and dist > HIGHWAY_EXIT_PREVIEW_M and \
     any(word in (text or "") for word in ("IC", "JC", "분기점", "출구")):
    # A highway exit still far away: the AVN's EXIT sign, not the arrow.
    return bytes([DISPLAY_HIGHWAY_EXIT, 0, 0, DIRECTION_NONE, hi, lo, unit, (BAR_NONE << 4) | tenths])
  code = TURN_CODES.get(turn_type)
  if code is None:
    return None
  display, direction, side = code
  return bytes([display, side >> 8, side & 0xFF, direction, hi, lo, unit,
                (bar_tenths(dist) << 4) | tenths])


def camera_payload(camera, v_ego_kph=0.0):
  """0x1E5 for a navi speed camera close enough to show, or None (leave the AVN's)."""
  if not isinstance(camera, dict):
    return None
  limit = float(camera.get("limit", 0.0) or 0.0)
  dist = float(camera.get("distance", -1.0) or -1.0)
  kind = int(camera.get("type", -1))
  show_m = CAMERA_SHOW_M_FAST if limit >= 80.0 else CAMERA_SHOW_M_SLOW
  if limit <= 0.0 or not 0.0 < dist <= show_m or kind not in (SDI_SIGNAL_SPEED, SDI_SPEED, SDI_MOBILE):
    return None
  step = max(1, min(15, int(round(limit / 10.0))))
  fixed = step if kind == SDI_SPEED else 0
  mobile = step if kind == SDI_MOBILE else 0
  signal = step if kind == SDI_SIGNAL_SPEED else 0
  over = 1 if v_ego_kph > limit else 0
  return bytes([(mobile << 4) | fixed, (signal << 4) | (over << 2), 0, 0, 0xF0, 0, 0x0F, 0xFF])


def text_payload(text):
  """HUD text message body: 0xF0 then UTF-16LE without terminator; the AVN clears
  the text with an empty string that is just a UTF-16 terminator (03 F0 00 00)."""
  return b"\xf0" + ((text or "").encode("utf-16-le") or b"\x00\x00")


def isotp_frames(body):
  """ISO-TP frames for one message. The HUD answers the first frame with a
  'clear to send, no block limit, 5 ms' flow control (0x4B9 30 00 05), so the
  consecutive frames are just paced, not gated on it."""
  if len(body) <= 7:
    return [bytes([len(body)]) + body + b"\xaa" * (7 - len(body))]
  frames = [bytes([0x10 | (len(body) >> 8), len(body) & 0xFF]) + body[:6]]
  rest, seq = body[6:], 1
  while rest:
    chunk, rest = rest[:7], rest[7:]
    frames.append(bytes([0x20 | (seq & 0xF)]) + chunk + b"\xaa" * (7 - len(chunk)))
    seq += 1
  return frames


class McanHud:
  # Wait this long after a first frame before the consecutive frames (the HUD's
  # flow control came back within a few ms in the logs).
  FC_WAIT_FRAMES = 3

  def __init__(self):
    self.text = None
    self.text_queue = []
    self.text_wait = 0
    self.active = False

  def _queue_text(self, text):
    self.text = text
    self.text_queue = isotp_frames(text_payload(text))
    self.text_wait = 0

  def update(self, frame, nav_state, v_ego=0.0):
    """CAN messages for this 100 Hz frame. nav_state: NavigationRouteData.update()."""
    msgs = []
    if frame % SEND_PERIOD_FRAMES == 0:
      camera = NavigationRouteData.speed_events(nav_state)["camera"] if isinstance(nav_state, dict) else None
      cam = camera_payload(camera, v_ego * 3.6)
      if cam is not None:
        msgs.append(make_can_msg(CAMERA_ADDR, cam, MCAN_BUS))
    payload = None
    rerouting = False
    # guidance_fresh, not fresh: "fresh" is false for straight/arrival guidance.
    if isinstance(nav_state, dict) and nav_state.get("guidance_fresh") and not nav_state.get("off_route"):
      nxt = nav_state.get("next") or {}
      payload = tbt_payload(int(nav_state.get("turn_type", -1)), float(nav_state.get("distance", -1.0)),
                            str(nav_state.get("text", "") or ""), int(nxt.get("turn_type", -1)))
    elif isinstance(nav_state, dict) and nav_state.get("guidance_stream_fresh") and nav_state.get("off_route"):
      # The navi app is still guiding but recalculating the route.
      payload = REROUTE_PAYLOAD
      rerouting = True

    if payload is None:
      if self.active:
        # Guidance gone: clear our text once and give the HUD back to the AVN.
        self._queue_text("")
        self.active = False
    else:
      self.active = True
      text = "" if rerouting else str(nav_state.get("text", "") or "")
      if text != self.text:
        self._queue_text(text)
      if frame % SEND_PERIOD_FRAMES == 0:
        dist = float(nav_state.get("distance", 0.0))
        msgs.append(make_can_msg(TBT_ADDR, payload, MCAN_BUS))
        percent = 100 if rerouting else bar_percent(dist)   # the AVN's idle bar while rerouting
        msgs.append(make_can_msg(BAR_ADDR, BAR_PREFIX_DEFAULT + bytes([percent]), MCAN_BUS))

    if self.text_queue:
      if self.text_wait > 0:
        self.text_wait -= 1
      else:
        first = self.text_queue.pop(0)
        msgs.append(make_can_msg(TEXT_ADDR, first, MCAN_BUS))
        if first[0] >> 4 == 1:
          self.text_wait = self.FC_WAIT_FRAMES
    return msgs
