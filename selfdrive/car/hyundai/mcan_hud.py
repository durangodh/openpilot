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
# The camera is shown for as long as the navi app lists it (Naver adds a city camera
# from about 670 m), so the HUD circle appears when the app's own alert does.
SDI_SIGNAL_SPEED = 0       # navi module SDI types (NaverCodes / TMAP)
SDI_SPEED = 1
SDI_MOBILE = 7
REAR_CAMERA_TEXT = "후면단속"
SECTION_TEXT = "구간단속"
# Non-speed notices: every camera nibble off (F), the notice in its own field. AVN 11-02
# 12:08:09 "교통정보수집" circle: byte 2 (Navi_NonSpdTrap) = 9.
NOTICE_PAYLOADS = {"traffic_info": bytes.fromhex("fff00900f00f0fff")}

DISPLAY_TURN = 13
DISPLAY_UTURN = 31
DISPLAY_DEST_AHEAD = 112
DISPLAY_ARRIVED = 3
DISPLAY_TOLLGATE = 178     # 0xB2 tollgate (2026-11-01 07:18 북오산, 07:44 정남톨게이트)
# Tollgate: beyond 400 m the HUD shows the purple TG card (0x115 bar off, 0x123 TG field 3);
# from 400 m the gate picture with the bar (0x123 TG field 1 + the toll: 00 5a 01 = 900원).
# From 1 km (on request) the gate picture, the fee when the navi gives one, and a 1 km bar
# (the AVN's from 1.2 km at 남논산, 2026-11-04 08:24: 0x123 = dist / 10, 0x115 bar 9 at 900 m,
# fee 02 44 = 5,800원). Before that the TG card with the tollgate name.
TOLLGATE_GATE_M = 1000
TOLLGATE_BAR_M = 1000
TOLL_WORDS = ("톨게이트", "요금소")
BAR_PREFIX_TOLLGATE = bytes.fromhex("00000040000000")   # TG 1, no toll known
DISPLAY_HIGHWAY_EXIT = 164 # 0xA4 highway EXIT sign preview, km distance (11-01 07:42 "1.7km")
DISPLAY_RAMP_TURN = 17     # turn off a ramp, no intersection drawn (11-01 07:43 "병점, 정남")
# The side-street word is a bitmap of the junction's road arms: bit 0 straight on, bit 4
# right, bit 11 left (bit 1 / 14 bear right / left). AVN: 0811 crossroads, 0810 T junction
# (11-01 07:44 "수원"), 0011 straight + right (11-02 12:20/12:26), 0801 straight + left.
ARM_STRAIGHT = 0x0001
ARM_RIGHT = 0x0010
ARM_LEFT = 0x0800
SIDE_T_JUNCTION = ARM_LEFT | ARM_RIGHT
# Navi lane arrows (lane_current "dirs", same in all three modules): 1 straight, 2 left, 4 right.
LANE_DIR_ARMS = ((1, ARM_STRAIGHT), (2, ARM_LEFT), (4, ARM_RIGHT))
TURN_ARM = {12: ARM_LEFT, 13: ARM_RIGHT}
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
# Naver ends its guidance near the goal ("목적지 부근") before the car is within
# ARRIVED_DIST_M, so the arrived flag never went out and the HUD went back to the AVN's
# idle screen. Guidance that stops within ARRIVE_END_M of the goal shows the flag for
# ARRIVED_HOLD_FRAMES like the AVN's arrival screen.
ARRIVE_END_M = 200
ARRIVED_HOLD_FRAMES = 500  # 5 s at 100 Hz
ARRIVED_PAYLOAD = bytes([DISPLAY_ARRIVED, 0, 0, 0, 0, 0, 0, 0xF0])
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
# Turns the AVN announces as "keep going + preview" until close: it draws the intersection
# only from 300-400 m in town (11-02 12:08 700 m 67 -> 400 m 13, 12:14 700 m -> 300 m), when
# the navi's lane guidance for that turn comes up; right after a highway exit, on the ramp,
# from the start (11-01 07:43 17 at 1.2 km).
PREVIEW_TURNS = (12, 13, 14, 16, 19)
# Underpasses: the AVN's own picture 46 (0x2E) with a direction, and a keep-going preview
# until 400 m (2026-11-05 12:50 AVN, same road as Naver 11-07 07:25-07:30):
#   신리IC side road right of the underpass (Naver SideUnderpassRight): 84 from 1.5 km, then 46/12
#   물향기지하차도 entry (Naver AccessUnderpassStraight): 91 from 1.8 km, then 46/36
#   오산IC underpass entry (Naver AccessUnderpassLeft): 46/36 (log started at 140 m)
DISPLAY_UNDERPASS = 46
UNDERPASS = {"side_right": (12, 84), "access_left": (36, 91), "access_straight": (36, 91)}
UNDERPASS_SHOW_M = 400
TURN_SHOW_M = 300
TURN_SHOW_LANE_M = 500     # with the navi's lanes for this turn, from up to here
LANE_MATCH_M = 50
# Naver/Kakao modules map the destination to 2, TMAP sends its own 201.
TURN_ARRIVE = (2, 201)

# The AVN's 0x123 bar is 0 % with keep-going (65-72) and EXIT sign frames (10-31..11-02 logs).
BAR_EMPTY_DISPLAYS = (DISPLAY_STRAIGHT, 66, 67, 70, 72, DISPLAY_HIGHWAY_EXIT, DISPLAY_ARRIVED)

# Fallback stock bytes 0-6 of 0x123 until the AVN's own frame has been seen.
BAR_PREFIX_DEFAULT = bytes.fromhex("000000c0ffff0f")


# The AVN's distance bar runs over 300 m for maneuvers announced close by, and over
# 1 km for ones announced from further away (2026-11-01 07:41-07:43: from 1.2 km it lost
# a step every 100 m and 0x123 read dist / 10; city turns from 300-400 m every 30 m and
# dist / 3).
BAR_SCALE_NEAR_M = 300
BAR_SCALE_FAR_M = 1000
BAR_FAR_START_M = 500


def bar_scale(start_m):
  return BAR_SCALE_FAR_M if start_m > BAR_FAR_START_M else BAR_SCALE_NEAR_M


def bar_tenths(dist_m, scale_m=BAR_SCALE_NEAR_M):
  """10-step bar of 0x115 byte 7: full beyond the scale, then one step per scale / 10."""
  if dist_m > scale_m:
    return 10
  return max(1, min(10, int(dist_m // (scale_m // 10))))


def bar_percent(dist_m, scale_m=BAR_SCALE_NEAR_M):
  return max(1, min(100, int(round(dist_m * 100.0 / scale_m))))


def distance_fields(dist):
  """0x115 bytes 4-5 (value), byte 6 (unit) and the low nibble of byte 7 (tenths).
  Metres below 1 km. From 1 km the AVN sends whole km in bytes 4-5, 0x10 in byte 6
  and the tenths in byte 7's low nibble: 00 42 10 .7 was "66.7km" and 00 14 10 .0
  "20.0km" on the HUD (2026-10-31 / 2026-11-01 logs)."""
  if dist < 1000:
    return dist >> 8, dist & 0xFF, 0, 0
  km = min(0xFFFF, dist // 1000)
  return km >> 8, km & 0xFF, 0x10, (dist % 1000) // 100


def is_highway_exit(turn_type, text):
  return turn_type == 18 and any(word in (text or "") for word in ("IC", "JC", "분기점", "출구"))


def lane_matches(lane, dist_m):
  """The navi's lane guidance is for this maneuver (its distance agrees with the turn's)."""
  if not isinstance(lane, dict):
    return False
  try:
    lane_dist = float(lane.get("distance_m", -1.0))
  except (TypeError, ValueError):
    return False
  return lane_dist > 0.0 and abs(lane_dist - dist_m) <= LANE_MATCH_M


def lane_for_junction(lane, dist_m):
  """Lanes usable for the junction shape: matching the turn's distance, or, when the navi
  gives no lane distance (Naver 6.11's lane item, distance getter unverified), lanes shown
  within TURN_SHOW_M of the turn, where the navi only shows the turn's own lanes."""
  if lane_matches(lane, dist_m):
    return True
  if not isinstance(lane, dict) or not lane.get("dirs"):
    return False
  try:
    lane_dist = float(lane.get("distance_m", -1.0))
  except (TypeError, ValueError):
    lane_dist = -1.0
  return lane_dist <= 0.0 and 0.0 < dist_m <= TURN_SHOW_M


def lane_side(turn_type, lane):
  """Junction shape for a left/right turn from the navi's lane arrows at that junction:
  crossroads when any lane goes straight on, otherwise a T junction (2026-11-14, as asked:
  the straight+turn shapes looked wrong next to the navi's lanes). None (keep the
  crossroads) without usable lanes."""
  if turn_type not in TURN_ARM or not isinstance(lane, dict) or not isinstance(lane.get("dirs"), list):
    return None
  dirs = 0
  for d in lane["dirs"]:
    if isinstance(d, int) and not isinstance(d, bool):
      dirs |= d
  if not dirs & (1 | 2 | 4):
    return None
  return SIDE_TURN if dirs & 1 else SIDE_T_JUNCTION


def show_turn(turn_type, dist_m, lane=None, after_exit=False):
  """Whether the AVN would draw this turn now, or still keep going with its preview."""
  if turn_type not in PREVIEW_TURNS or after_exit or dist_m <= TURN_SHOW_M:
    return True
  return dist_m <= TURN_SHOW_LANE_M and lane_matches(lane, dist_m)


def tbt_payload(turn_type, dist_m, text="", next_turn_type=-1, scale_m=BAR_SCALE_NEAR_M, after_exit=False,
                tollgate=False, preview=False, junction=None, underpass=""):
  """0x115 payload for one Naver maneuver, or None to leave the AVN's frame alone.
  next_turn_type picks the keep-going preview icon; a 톨게이트 name on a straight
  maneuver shows the tollgate icon (the modules map tollgates to straight); tollgate=True
  (the module's explicit flag) shows it whatever the turn code or name; preview=True keeps
  going with this turn as the preview (see show_turn); junction overrides the shape of
  a left/right turn (lane_side)."""
  dist = max(0, min(0xFFFFFF, int(round(dist_m))))
  hi, lo, unit, tenths = distance_fields(dist)
  if turn_type in TURN_ARRIVE:
    if dist <= ARRIVED_DIST_M:
      return bytes([DISPLAY_ARRIVED, 0, 0, 0, 0, 0, 0, 0xF0])
    return bytes([DISPLAY_DEST_AHEAD, 0, 0, 0, hi, lo, unit, (BAR_NONE << 4) | tenths])
  if tollgate or (turn_type == TURN_STRAIGHT and "톨게이트" in (text or "")):
    bar = BAR_NONE if dist > TOLLGATE_GATE_M else bar_tenths(dist, TOLLGATE_BAR_M)
    return bytes([DISPLAY_TOLLGATE, 0, 0, DIRECTION_NONE, hi, lo, unit, (bar << 4) | tenths])
  if underpass in UNDERPASS:
    direction, preview_display = UNDERPASS[underpass]
    if preview:
      return bytes([preview_display, 0, 0, DIRECTION_NONE, hi, lo, unit, (BAR_NONE << 4) | tenths])
    return bytes([DISPLAY_UNDERPASS, 0, 0, direction, hi, lo, unit, (bar_tenths(dist, scale_m) << 4) | tenths])
  if turn_type == TURN_STRAIGHT:
    display = STRAIGHT_PREVIEW.get(next_turn_type, DISPLAY_STRAIGHT)
    return bytes([display, 0, 0, DIRECTION_NONE, hi, lo, unit, (BAR_NONE << 4) | tenths])
  if preview and turn_type in STRAIGHT_PREVIEW:
    return bytes([STRAIGHT_PREVIEW[turn_type], 0, 0, DIRECTION_NONE, hi, lo, unit, (BAR_NONE << 4) | tenths])
  if is_highway_exit(turn_type, text) and dist > HIGHWAY_EXIT_PREVIEW_M:
    # A highway exit still far away: the AVN's EXIT sign, not the arrow.
    return bytes([DISPLAY_HIGHWAY_EXIT, 0, 0, DIRECTION_NONE, hi, lo, unit, (BAR_NONE << 4) | tenths])
  code = TURN_CODES.get(turn_type)
  if code is None:
    return None
  display, direction, side = code
  if turn_type in (12, 13):
    if after_exit:
      # The turn right after leaving the highway, on the ramp: the AVN's ramp turn
      # picture without an intersection (17 / 0x0000, 2026-11-01 07:43 "병점, 정남").
      display, side = DISPLAY_RAMP_TURN, 0
    elif junction is not None:
      side = junction
  return bytes([display, side >> 8, side & 0xFF, direction, hi, lo, unit,
                (bar_tenths(dist, scale_m) << 4) | tenths])


def tollgate_bar_payload(dist_m, toll_fee_won=None):
  """0x123: TG card >400 m, gate <=400 m; current-point KRW fee only.

  AVN 07:44:11: 00 00 00 40 00 5a 01 64 accompanied the 900-won photo.
  Unknown/zero/unrepresentable fees deliberately use the observed no-fee frame.
  No caching: changing guidance or losing the fee clears the previous amount.
  """
  if dist_m > TOLLGATE_GATE_M:
    return BAR_PREFIX_DEFAULT + b"\x00"
  fee = NavigationRouteData.toll_fee_won({"toll_fee_won": toll_fee_won})
  prefix = BAR_PREFIX_TOLLGATE
  if fee is not None:
    prefix = prefix[:4] + (fee // 10).to_bytes(2, "big") + b"\x01"
  return prefix + bytes([bar_percent(dist_m, TOLLGATE_BAR_M)])


def next_tollgate(nav_state):
  """(distance from the car, name) of a tollgate that is the navi's next item after a keep-going
  one (e.g. a rest area), or None. The AVN does not stop at rest areas: on 2026-11-04 it showed
  풍세톨게이트 while Naver's current item was 이인휴게소."""
  if int(nav_state.get("turn_type", -1)) != TURN_STRAIGHT or nav_state.get("tollgate"):
    return None
  nxt = nav_state.get("next")
  if not isinstance(nxt, dict):
    return None
  text = str(nxt.get("text", "") or "")
  if not (nxt.get("tollgate") or (int(nxt.get("turn_type", -1)) == TURN_STRAIGHT and
                                  any(w in text for w in TOLL_WORDS))):
    return None
  # The next item's distance after the current one (carrot_navi_server makes Naver's
  # segment-relative; TMAP keeps the vehicle distance and adds display_distance_m).
  segment = float(nxt.get("segment", nxt.get("distance", -1.0)))
  dist = float(nav_state.get("distance", -1.0))
  if segment < 0.0 or dist < 0.0:
    return None
  return dist + segment, text


def camera_payload(camera, v_ego_kph=0.0):
  """0x1E5 for the speed camera the navi app currently alerts, or None (leave the AVN's)."""
  if not isinstance(camera, dict):
    return None
  limit = float(camera.get("limit", 0.0) or 0.0)
  dist = float(camera.get("distance", -1.0) or -1.0)
  kind = int(camera.get("type", -1))
  if limit <= 0.0 or dist <= 0.0 or kind not in (SDI_SIGNAL_SPEED, SDI_SPEED, SDI_MOBILE):
    return None
  step = max(1, min(15, int(round(limit / 10.0))))
  fixed = step if kind == SDI_SPEED else 0
  mobile = step if kind == SDI_MOBILE else 0
  signal = step if kind == SDI_SIGNAL_SPEED else 0
  over = 1 if v_ego_kph > limit else 0
  return bytes([(mobile << 4) | fixed, (signal << 4) | (over << 2), 0, 0, 0xF0, 0, 0x0F, 0xFF])


def section_payload(section, v_ego_kph=0.0):
  """0x1E5 while inside a section-control (구간단속) zone, or None.

  The AVN shows a section zone on the HUD as a plain fixed-camera limit circle for the whole
  zone: 2026-11-04 08:15:50-08:16:34 (limit 100, avg 89, 10 km left on the AVN) every 0x1E5
  was 0a000000f0000fff, the same frame as a fixed 100 camera, with no average or distance.
  """
  if not isinstance(section, dict):
    return None
  limit = float(section.get("limit", 0.0) or 0.0)
  dist = float(section.get("distance", -1.0) or -1.0)
  return camera_payload({"type": SDI_SPEED, "limit": limit, "distance": dist}, v_ego_kph)


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
  # The AVN sends a new text ~120 ms after the 0x115 of the new maneuver (2026-11-04 08:24:42
  # 남논산: 0x115 -> B2 at .832, 0x4A9 "남논산톨게이트" at .952). Ours went out in the same
  # cycle, often before the new 0x115 (sent every 5th frame), and the HUD showed no name on a
  # TG card (09:11 풍세상톨게이트). Start the text after the new 0x115 like the AVN.
  TEXT_DELAY_FRAMES = 12

  def __init__(self):
    self.text = None
    self.text_queue = []
    self.text_wait = 0
    self.active = False
    self.maneuver = None
    self.maneuver_start = 0.0
    self.prev_exit = False      # the maneuver before the current one was a highway exit
    self.cur_exit = False
    self.shown = False          # the current turn is drawn (not the keep-going preview)
    self.junction = None        # junction shape from the navi's lanes at this turn
    self.goal_frame = None      # last frame guidance was to the goal within ARRIVE_END_M
    self.arrived_until = -1     # arrived flag held after the guidance ended at the goal

  def _queue_text(self, text):
    self.text = text
    self.text_queue = isotp_frames(text_payload(text))
    self.text_wait = self.TEXT_DELAY_FRAMES

  def update(self, frame, nav_state, v_ego=0.0):
    """CAN messages for this 100 Hz frame. nav_state: NavigationRouteData.update()."""
    msgs = []
    events = NavigationRouteData.speed_events(nav_state) if isinstance(nav_state, dict) else {}
    camera = events.get("camera")
    section = events.get("section")
    cam = camera_payload(camera, v_ego * 3.6)
    # The AVN's map has no rear-facing cameras and the HUD no picture for them: while the navi
    # alerts one, its circle shows the limit as usual and the text line says so.
    rear_camera = cam is not None and bool(camera.get("rear"))
    in_section = False
    if cam is None:
      cam = section_payload(section, v_ego * 3.6)
      in_section = cam is not None
    if cam is None:
      notice = NavigationRouteData.speed_notice(nav_state)
      cam = NOTICE_PAYLOADS.get(notice["kind"]) if notice is not None else None
    if frame % SEND_PERIOD_FRAMES == 0 and cam is not None:
      msgs.append(make_can_msg(CAMERA_ADDR, cam, MCAN_BUS))
    payload = None
    rerouting = False
    dist = float(nav_state.get("distance", 0.0)) if isinstance(nav_state, dict) else 0.0
    text = str(nav_state.get("text", "") or "") if isinstance(nav_state, dict) else ""
    tollgate = bool(nav_state.get("tollgate")) if isinstance(nav_state, dict) else False
    toll_fee_won = nav_state.get("toll_fee_won") if isinstance(nav_state, dict) else None
    # guidance_fresh, not fresh: "fresh" is false for straight/arrival guidance.
    if isinstance(nav_state, dict) and nav_state.get("guidance_fresh") and not nav_state.get("off_route"):
      nxt = nav_state.get("next") or {}
      turn_type = int(nav_state.get("turn_type", -1))
      dist = float(nav_state.get("distance", -1.0))
      text = str(nav_state.get("text", "") or "")
      ahead = next_tollgate(nav_state)
      if ahead is not None:
        dist, text = ahead
        tollgate, toll_fee_won = True, None
      if (turn_type, text) != self.maneuver:
        self.prev_exit = self.cur_exit
        self.cur_exit = is_highway_exit(turn_type, text)
        self.maneuver = (turn_type, text)
        self.shown = False
        self.junction = None
      lane = nav_state.get("lane_current")
      if lane_for_junction(lane, dist):
        # Kept for the rest of the maneuver: the lanes go away close to the junction.
        self.junction = lane_side(turn_type, lane) or self.junction
      # Far turns keep going with their preview until the AVN would draw them; once drawn
      # they stay drawn (a lane flicker must not bring the preview back).
      underpass = "" if ahead is not None else str(nav_state.get("underpass") or "")
      if underpass in UNDERPASS:
        shown = self.shown or dist <= UNDERPASS_SHOW_M
      else:
        shown = self.shown or show_turn(turn_type, dist, lane, self.prev_exit)
      # The bar scale follows how far away this maneuver was first drawn.
      if (shown and not self.shown) or dist > self.maneuver_start + 100.0:
        self.maneuver_start = dist
      self.shown = shown
      payload = tbt_payload(turn_type, dist, text, int(nxt.get("turn_type", -1)),
                            bar_scale(self.maneuver_start), after_exit=self.prev_exit,
                            tollgate=tollgate, preview=not shown, underpass=underpass,
                            junction=self.junction)
    elif isinstance(nav_state, dict) and nav_state.get("guidance_stream_fresh") and nav_state.get("off_route"):
      # The navi app is still guiding but recalculating the route.
      payload = REROUTE_PAYLOAD
      rerouting = True

    if payload is not None and payload[0] in (DISPLAY_DEST_AHEAD, DISPLAY_ARRIVED) \
        and dist <= ARRIVE_END_M:
      self.goal_frame = frame
      self.arrived_until = -1
    elif payload is None and self.goal_frame is not None:
      if frame - self.goal_frame <= 100:
        self.arrived_until = frame + ARRIVED_HOLD_FRAMES
      self.goal_frame = None
    elif payload is not None:
      self.goal_frame = None
      self.arrived_until = -1
    if payload is None and frame < self.arrived_until:
      payload = ARRIVED_PAYLOAD
      text = ""

    if payload is None:
      if self.active:
        # Guidance gone: clear our text once and give the HUD back to the AVN.
        self._queue_text("")
        self.active = False
    else:
      self.active = True
      text = "" if rerouting else text
      if rear_camera and not rerouting:
        text = REAR_CAMERA_TEXT
      elif in_section and not rerouting:
        # The AVN only shows the limit circle in a section zone; say what it is on the text line.
        text = SECTION_TEXT
      if text != self.text:
        self._queue_text(text)
      if frame % SEND_PERIOD_FRAMES == 0:
        msgs.append(make_can_msg(TBT_ADDR, payload, MCAN_BUS))
        # the AVN's idle bar while rerouting
        percent = 100 if rerouting else bar_percent(dist, bar_scale(self.maneuver_start))
        bar_payload = BAR_PREFIX_DEFAULT + bytes([percent])
        if payload[0] in BAR_EMPTY_DISPLAYS:
          bar_payload = BAR_PREFIX_DEFAULT + b"\x00"
        if payload[0] == DISPLAY_TOLLGATE:
          bar_payload = tollgate_bar_payload(dist, toll_fee_won)
        msgs.append(make_can_msg(BAR_ADDR, bar_payload, MCAN_BUS))

    if self.text_queue:
      if self.text_wait > 0:
        self.text_wait -= 1
      else:
        first = self.text_queue.pop(0)
        msgs.append(make_can_msg(TEXT_ADDR, first, MCAN_BUS))
        if first[0] >> 4 == 1:
          self.text_wait = self.FC_WAIT_FRAMES
    return msgs
