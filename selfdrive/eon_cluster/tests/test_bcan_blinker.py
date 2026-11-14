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
  assert link_state(ok, 5.0) == LINK_NONE
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
