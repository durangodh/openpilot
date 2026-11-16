"""DH B-CAN blinker (bcan_blinker_core.py): protocol, lane-change tap timing and HUD status."""
import json

from selfdrive.eon_cluster.bcan_blinker_core import (LINK_ERROR, LINK_NO_BCAN, LINK_NONE, LINK_OK, MAX_TAPS,
                                                     TAP_REPEAT_S, LaneTapPlanner, command_packet, hud_status,
                                                     link_state, parse_status, write_status)

STATUS = b'{"t":"dhbl","v":1,"rssi":-55,"bcan":1,"lamp":0,"err":0,"seq":3,"res":"ok"}'


def test_command_packet():
  assert command_packet(12, "L") == b"DHBL1 12 L"
  assert command_packet(65537, "R") == b"DHBL1 1 R"


def test_parse_status():
  assert parse_status(STATUS) == {"rssi": -55, "bcan": 1, "lamp": 0, "err": 0, "seq": 3, "res": "ok"}
  assert parse_status(b"not json") is None
  assert parse_status(b'{"t":"other","v":1}') is None
  assert parse_status(b'{"t":"dhbl","v":2}') is None
  assert parse_status(b'\xff\xfe') is None


def test_link_state():
  ok = parse_status(STATUS)
  assert link_state(ok, 0.5) == LINK_OK
  assert link_state(ok, 7.0) == LINK_NONE
  assert link_state(None, None) == LINK_NONE
  assert link_state(dict(ok, bcan=0), 0.5) == LINK_NO_BCAN
  assert link_state(dict(ok, err=1), 0.5) == LINK_ERROR


def run(planner, start, seconds, **kw):
  args = dict(changing=True, direction="L", left_lamp=False, right_lamp=False)
  args.update(kw)
  taps = []
  t = start
  while t < start + seconds:
    side = planner.update(t, **args)
    if side:
      taps.append((round(t - start, 2), side))
    t += 0.05
  return taps


def test_one_tap_per_short_lane_change():
  p = LaneTapPlanner()
  taps = run(p, 100.0, 3.0)
  assert taps == [(0.0, "L")]


def test_long_lane_change_repeats_and_is_capped():
  p = LaneTapPlanner()
  taps = run(p, 100.0, 30.0, direction="R")
  assert len(taps) == MAX_TAPS
  assert all(side == "R" for _, side in taps)
  assert taps[1][0] - taps[0][0] >= TAP_REPEAT_S


def test_our_own_lamp_does_not_look_like_the_driver():
  p = LaneTapPlanner()
  assert run(p, 100.0, 1.0) == [(0.0, "L")]
  run(p, 101.0, 0.5, changing=False)
  # next lane change 2 s later while our flashes are still on: still ours to signal
  assert run(p, 103.0, 0.2, left_lamp=True) == [(0.0, "L")]


def test_driver_lever_lane_change_is_left_alone():
  p = LaneTapPlanner()
  assert run(p, 100.0, 10.0, left_lamp=True) == []
  assert run(LaneTapPlanner(), 100.0, 10.0, direction="R", right_lamp=True) == []


def test_no_tap_without_lane_change_or_direction():
  assert run(LaneTapPlanner(), 100.0, 5.0, changing=False) == []
  assert run(LaneTapPlanner(), 100.0, 5.0, direction=None) == []


def test_direction_change_starts_a_new_lane_change():
  p = LaneTapPlanner()
  assert run(p, 100.0, 1.0) == [(0.0, "L")]
  assert run(p, 101.0, 1.0, direction="R") == [(0.0, "R")]


def test_hud_status(tmp_path):
  path = str(tmp_path / "status.json")
  write_status(path, {"wall": 1000.0, "link": LINK_OK, "rssi": -60,
                      "event": {"seq": 4, "dir": "L", "wall": 999.0, "ok": True, "why": ""}})
  assert hud_status(path, clock=lambda: 1001.0) == {"link": LINK_OK, "rssi": -60, "seq": 4, "dir": "L",
                                                     "ok": True, "why": "", "age": 2000}
  # event older than 3 s: link only
  assert hud_status(path, clock=lambda: 1002.5) == {"link": LINK_OK, "rssi": -60}
  # stalled writer or feature off
  assert hud_status(path, clock=lambda: 1004.0) is None
  assert hud_status(str(tmp_path / "missing.json")) is None
  with open(path, "w") as f:
    json.dump([1, 2], f)
  assert hud_status(path, clock=lambda: 1000.0) is None


def test_lane_direction_from_enum_names():
  from selfdrive.eon_cluster.bcan_blinker_core import lane_direction

  class Enum:  # stands in for a capnp _DynamicEnum, which str()s to its name
    def __init__(self, name):
      self.name = name

    def __str__(self):
      return self.name

  assert lane_direction(Enum("left")) == "L"
  assert lane_direction(Enum("right")) == "R"
  assert lane_direction(Enum("none")) is None
  assert lane_direction(None) is None


def test_cancel_at_line_crossing_fallback_or_finishing():
  from selfdrive.eon_cluster.bcan_blinker_core import BlinkCancelPlanner, command_packet
  c = BlinkCancelPlanner()
  assert not c.update(0.0, "preLaneChange", "L")
  assert not c.update(1.0, "laneChangeStarting", None)
  assert c.update(1.6, "laneChangeStarting", None, crossed=True)
  assert not c.update(1.7, "laneChangeStarting", None, crossed=True)      # once
  assert not c.update(5.0, "laneChangeFinishing", None)
  # no lane lines: 2 s into laneChangeStarting
  c = BlinkCancelPlanner()
  c.update(0.0, "preLaneChange", "R")
  assert not c.update(1.0, "laneChangeStarting", None)
  assert not c.update(2.9, "laneChangeStarting", None)
  assert c.update(3.0, "laneChangeStarting", None)
  # a lane change we did not tap for (driver's lever) is never cancelled
  c = BlinkCancelPlanner()
  assert not any(c.update(t, s, None, True) for t, s in ((0, "preLaneChange"), (1, "laneChangeStarting"),
                                                          (9, "laneChangeFinishing")))
  # back to off first (lane change abandoned): nothing pending
  c = BlinkCancelPlanner()
  c.update(0.0, "preLaneChange", "R")
  c.update(5.0, "off", None)            # off past the remote-press grace
  assert not c.update(6.0, "laneChangeFinishing", None)
  assert command_packet(7, "C") == b"DHBL1 7 C"


def test_lane_cross_detector():
  from selfdrive.eon_cluster.bcan_blinker_core import LaneCrossDetector
  d = LaneCrossDetector()
  ys = [1.8, 1.5, 1.2, 0.9, 0.6, 0.3, 3.4, 3.3]      # left line closes in, then the next lane's line
  hits = [d.update(y, 0.9) for y in ys]
  assert hits == [False] * 6 + [True, False]
  d = LaneCrossDetector()
  assert not any(d.update(y, 0.9) for y in [1.8, 1.7, 3.5])          # jump while still far: no
  d = LaneCrossDetector()
  assert not any(d.update(y, p) for y, p in [(0.5, 0.9), (3.4, 0.1)])  # line lost: no


def test_stopped_blink_requests_and_actions(tmp_path):
  import json
  from selfdrive.eon_cluster.bcan_blinker_core import BlinkRequestSource, stopped_blink_action
  t = [100.0]
  path = str(tmp_path / "blink.json")
  src = BlinkRequestSource(path, clock=lambda: t[0])
  assert src.poll() is None
  t[0] = 101.0
  with open(path, "w") as f:
    json.dump({"direction": -1, "ts": 100.9, "id": "a"}, f)
  assert src.poll() == "L"
  assert src.poll() is None                      # each request once
  with open(path, "w") as f:
    json.dump({"direction": 1, "ts": 99.0, "id": "b"}, f)
  assert src.poll() is None                      # older than this process / stale
  # first press taps, a second press while it blinks ends it, later presses tap again
  assert stopped_blink_action("L", None, 10.0) == "L"
  assert stopped_blink_action("R", 10.0, 12.0) == "C"
  assert stopped_blink_action("L", 10.0, 14.0) == "L"
  assert stopped_blink_action(None, 10.0, 11.0) is None
  # during a lane change a second press is not a blinker cancel
  assert stopped_blink_action("L", 10.0, 12.0, lane_change_active=True) is None


def test_remote_press_tap_counts_for_the_lane_change():
  from selfdrive.eon_cluster.bcan_blinker_core import BlinkCancelPlanner, LaneTapPlanner
  p = LaneTapPlanner()
  p.note_external_tap(10.0, "L")
  # lamp already on from the press: not the driver's lever, and no second tap at once
  assert p.update(10.5, True, "L", True, False) is None
  assert not p.driver_owned
  assert p.update(15.0, True, "L", True, False) == "L"     # long lane change: repeat
  c = BlinkCancelPlanner()
  assert not c.update(10.0, "off", "L")                     # press, lane change not yet started
  assert not c.update(10.2, "preLaneChange", None)
  assert not c.update(11.0, "laneChangeStarting", None)
  assert c.update(11.5, "laneChangeStarting", None, crossed=True)
  c = BlinkCancelPlanner()
  c.update(10.0, "off", "L")
  assert not c.update(20.0, "off", None)                    # no lane change followed
  assert not c.update(21.0, "laneChangeStarting", None, crossed=True)


def test_hold_mode_press_actions():
  from selfdrive.eon_cluster.bcan_blinker_core import command_packet, hold_press_action
  assert hold_press_action("L", None) == "HL"
  assert hold_press_action("R", "L") == "C"        # the other button stops it
  assert hold_press_action("L", "L") is None       # same button: keeps holding
  assert hold_press_action(None, "L") is None
  assert command_packet(3, "HR") == b"DHBL1 3 HR"
  assert command_packet(4, "KL") == b"DHBL1 4 KL"
