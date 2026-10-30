"""Naver turn-by-turn on the Genesis DH stock HUD over MM-CAN (panda bus 1).

The head unit (AVN) feeds the HUD and cluster with (opendbc hyundai_2015_mcan.dbc,
checked against our 2026-10-31 M-CAN logs):
  0x115 HU_CLU_PE_02  [display type, side street(2), direction, distance m(2), 0, bar<<4]
                      direction = clockwise turn angle / 7.5 deg (right 12, left 36)
                      display type: 13 turn, 31 U-turn, 70 keep going, 112 destination
                      ahead, 3 arrived, 8 no guidance (idle, sent at 5 Hz all the time)
  0x123 HU_CLU_PE_07  byte 7 = distance bar in percent
  0x4A9 (ISO-TP)      HUD text: 0xF0 + UTF-16LE (direction/road name); HUD answers 0x4B9

The AVN keeps sending its idle 0x115 even without a destination, so we send ours at
50 Hz: the AVN value is on the bus for at most one 20 ms period out of its 200 ms.
Sending requires the panda's bus 1 to be live (McanHudTbt, through an MM-CAN
low-speed/fault-tolerant <-> high-speed converter); the panda only lets these three
IDs out on bus 1.
"""
from selfdrive.car import make_can_msg

MCAN_BUS = 1
# panda safety param bit that makes bus 1 live (board/safety/safety_defaults.h)
MCAN_TX_PARAM = 0x0400
TBT_ADDR = 0x115
BAR_ADDR = 0x123
TEXT_ADDR = 0x4A9

DISPLAY_TURN = 13
DISPLAY_UTURN = 31
DISPLAY_STRAIGHT = 70
DISPLAY_DEST_AHEAD = 112
DISPLAY_ARRIVED = 3
DIRECTION_NONE = 15        # the AVN's direction byte on keep-going / reroute frames
SIDE_TURN = 0x0811         # the AVN's most common intersection-shape value for turns
BAR_NONE = 0xF
ARRIVED_DIST_M = 15
SEND_PERIOD_FRAMES = 2     # 50 Hz from the 100 Hz carcontroller

# Naver module turn_type (TMAP-style codes, NaverCodes.java) -> (display, direction)
TURN_CODES = {
  12: (DISPLAY_TURN, 36),    # left (9 o'clock)
  13: (DISPLAY_TURN, 12),    # right (3 o'clock)
  14: (DISPLAY_UTURN, 36),   # U-turn, as the AVN draws it (2026-10-31 08:39 drive)
  16: (DISPLAY_TURN, 32),    # 8 o'clock
  17: (DISPLAY_TURN, 40),    # 10 o'clock / keep left
  18: (DISPLAY_TURN, 8),     # 2 o'clock / keep right
  19: (DISPLAY_TURN, 16),    # 4 o'clock
  20: (DISPLAY_TURN, 44),    # lane change left
  21: (DISPLAY_TURN, 4),     # lane change right
}
TURN_STRAIGHT = 11
TURN_ARRIVE = 2

# Fallback stock bytes 0-6 of 0x123 until the AVN's own frame has been seen.
BAR_PREFIX_DEFAULT = bytes.fromhex("000000c0ffff0f")


def bar_tenths(dist_m):
  """10-step bar of 0x115 byte 7, as the AVN does it: full beyond 300 m, 30 m per step."""
  if dist_m > 300:
    return 10
  return max(1, min(10, int(dist_m // 30)))


def bar_percent(dist_m):
  return max(1, min(100, int(round(dist_m / 3.0))))


def tbt_payload(turn_type, dist_m):
  """0x115 payload for one Naver maneuver, or None to leave the AVN's frame alone."""
  dist = max(0, min(0xFFFF, int(round(dist_m))))
  if turn_type == TURN_ARRIVE:
    if dist <= ARRIVED_DIST_M:
      return bytes([DISPLAY_ARRIVED, 0, 0, 0, 0, 0, 0, 0xF0])
    return bytes([DISPLAY_DEST_AHEAD, 0, 0, 0, dist >> 8, dist & 0xFF, 0, BAR_NONE << 4])
  if turn_type == TURN_STRAIGHT:
    return bytes([DISPLAY_STRAIGHT, 0, 0, DIRECTION_NONE, dist >> 8, dist & 0xFF, 0, BAR_NONE << 4])
  code = TURN_CODES.get(turn_type)
  if code is None:
    return None
  display, direction = code
  side = 0 if display == DISPLAY_UTURN else SIDE_TURN
  return bytes([display, side >> 8, side & 0xFF, direction, dist >> 8, dist & 0xFF, 0,
                bar_tenths(dist) << 4])


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

  def update(self, frame, nav_state):
    """CAN messages for this 100 Hz frame. nav_state: NavigationRouteData.update()."""
    msgs = []
    payload = None
    # guidance_fresh, not fresh: "fresh" is false for straight/arrival guidance.
    if isinstance(nav_state, dict) and nav_state.get("guidance_fresh") and not nav_state.get("off_route"):
      payload = tbt_payload(int(nav_state.get("turn_type", -1)), float(nav_state.get("distance", -1.0)))

    if payload is None:
      if self.active:
        # Guidance gone: clear our text once and give the HUD back to the AVN.
        self._queue_text("")
        self.active = False
    else:
      self.active = True
      text = str(nav_state.get("text", "") or "")
      if text != self.text:
        self._queue_text(text)
      if frame % SEND_PERIOD_FRAMES == 0:
        dist = float(nav_state.get("distance", 0.0))
        msgs.append(make_can_msg(TBT_ADDR, payload, MCAN_BUS))
        msgs.append(make_can_msg(BAR_ADDR, BAR_PREFIX_DEFAULT + bytes([bar_percent(dist)]), MCAN_BUS))

    if self.text_queue:
      if self.text_wait > 0:
        self.text_wait -= 1
      else:
        first = self.text_queue.pop(0)
        msgs.append(make_can_msg(TEXT_ADDR, first, MCAN_BUS))
        if first[0] >> 4 == 1:
          self.text_wait = self.FC_WAIT_FRAMES
    return msgs
