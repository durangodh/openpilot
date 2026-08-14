import json
import tempfile
import time

from selfdrive.controls.lib.carrot_navi_atc import CarrotNaviAtc, AtcForkLaneChangeController


def state_with_speed(speed, off_route=False):
  return {"speed_fresh": True, "speed": speed, "off_route": off_route}


def test_7714_primary_camera_and_bump_are_projected():
  camera = CarrotNaviAtc.speed_events(state_with_speed({
    "sdi": {"type": 1, "distance_m": 420, "speed_limit_kph": 60},
  }))
  assert camera["camera"] == {"type": 1, "distance": 420.0, "limit": 60.0}

  bump = CarrotNaviAtc.speed_events(state_with_speed({
    "sdi": {"type": 22, "distance_m": 93},
  }))
  assert bump["camera"] == {"type": 22, "distance": 93.0, "limit": 0.0}


def test_7714_explicit_and_block_sections_are_projected():
  explicit = CarrotNaviAtc.speed_events(state_with_speed({
    "section": {"active": True, "speed_limit_kph": 80, "remaining_distance_m": 2345},
  }))
  assert explicit["section"] == {"distance": 2345.0, "limit": 80.0}

  block = CarrotNaviAtc.speed_events(state_with_speed({
    "sdi": {"type": 1, "speed_limit_kph": 60, "block_type": 2,
            "block_speed_kph": 50, "block_distance_m": 390},
  }))
  assert block["section"] == {"distance": 390.0, "limit": 50.0}
  assert block["camera"] is None


def test_7714_secondary_bump_is_used_only_without_primary_camera():
  secondary = CarrotNaviAtc.speed_events(state_with_speed({
    "sdi_secondary": {"type": 22, "distance_m": 80},
  }))
  assert secondary["camera"] == {"type": 22, "distance": 80.0, "limit": 0.0}


def test_off_route_suppresses_all_7714_speed_events():
  blocked = CarrotNaviAtc.speed_events(state_with_speed({
    "sdi": {"type": 1, "distance_m": 100, "speed_limit_kph": 30},
    "section": {"active": True, "speed_limit_kph": 80, "remaining_distance_m": 1000},
  }, off_route=True))
  assert blocked == {"camera": None, "section": None}


def test_update_off_route_blocks_turn_route_and_speed_control():
  now_ms = int(time.time() * 1000)
  root = {
    "stream_updated_at_ms": {
      "guidance_current": now_ms, "guidance_next": now_ms,
      "route": now_ms, "vehicle": now_ms, "speed": now_ms,
      "navigation_status": now_ms,
    },
    "guidance_current": {"turn_type": 12, "distance_m": 80},
    "guidance_next": {"turn_type": 13, "distance_m": 400},
    "route": {"polyline": []},
    "vehicle": {"lat": 37.5, "lon": 127.1},
    "speed": {"sdi": {"type": 1, "distance_m": 100, "speed_limit_kph": 30}},
    "navigation_status": {"guidance_active": True, "off_route": True},
  }
  with tempfile.NamedTemporaryFile(mode="w+") as state_file:
    json.dump(root, state_file)
    state_file.flush()
    state = CarrotNaviAtc(state_file.name).update()

  assert state["off_route"]
  assert not state["fresh"]
  assert state["next"] is None
  assert not state["route_fresh"]
  assert not state["speed_fresh"]


def test_next_maneuver_speed_limit_is_independent_from_steering():
  current = {"fresh": True, "kind": "fork", "direction": 1,
             "distance": 120.0, "turn_type": 6, "text": "exit"}
  following = {"fresh": True, "kind": "turn", "direction": 1,
               "distance": 300.0, "turn_type": 13, "text": "right"}
  current["next"] = following

  current_limit, next_limit = CarrotNaviAtc.speed_limits_kph(current, 30.0, 6.0)
  assert current_limit is None
  assert next_limit is not None
  assert CarrotNaviAtc.steering_request(current, 10.0) == 0


def test_stale_next_maneuver_has_no_speed_limit():
  current = {"fresh": True, "kind": "turn", "direction": -1,
             "distance": 100.0, "turn_type": 12, "text": "left", "next": None}
  current_limit, next_limit = CarrotNaviAtc.speed_limits_kph(current)
  assert current_limit is not None
  assert next_limit is None


def test_map_curve_speed_calculation_is_cached_at_5hz():
  class CountingNavi(CarrotNaviAtc):
    def __init__(self):
      super().__init__()
      self.calls = 0

    def map_curve_speed_kph(self, *_args, **_kwargs):
      self.calls += 1
      return 40.0 + self.calls

  navi = CountingNavi()

  assert navi.cached_map_curve_speed_kph({}, 60.0, now=0.0) == 41.0
  assert navi.cached_map_curve_speed_kph({}, 60.0, now=0.10) == 41.0
  assert navi.calls == 1

  assert navi.cached_map_curve_speed_kph({}, 60.0, now=0.21) == 42.0
  assert navi.calls == 2


def fork_state(distance=300.0, direction=1, turn_type=6, text="right exit"):
  return {"fresh": True, "kind": "fork", "direction": direction,
          "distance": distance, "turn_type": turn_type, "text": text}


def confirm(controller, state, v_ego, right_lane_open, **kwargs):
  result = 0
  for _ in range(controller.CONFIRM_FRAMES):
    result = controller.update(state, v_ego, right_lane_open, **kwargs)
  return result


def test_right_exit_arms_closed_then_requests_when_lane_opens():
  controller = AtcForkLaneChangeController()
  assert confirm(controller, fork_state(340), 30.0, right_lane_open=False) == 0
  assert confirm(controller, fork_state(320), 30.0, right_lane_open=True) == 1


def test_does_not_start_when_exit_lane_was_already_open():
  controller = AtcForkLaneChangeController()
  assert confirm(controller, fork_state(320), 30.0, right_lane_open=True) == 0


def test_left_fork_is_not_automatic():
  controller = AtcForkLaneChangeController()
  assert confirm(controller, fork_state(direction=-1), 30.0, right_lane_open=False) == 0
  assert confirm(controller, fork_state(250, direction=-1), 30.0, right_lane_open=True) == 0


def test_driver_cancel_latches_for_current_exit():
  controller = AtcForkLaneChangeController()
  confirm(controller, fork_state(340), 30.0, right_lane_open=False)
  assert confirm(controller, fork_state(320), 30.0, right_lane_open=True, driver_cancel=True) == 0
  assert controller.update(fork_state(280), 30.0, right_lane_open=True) == 0


def test_completed_change_does_not_repeat():
  controller = AtcForkLaneChangeController()
  confirm(controller, fork_state(340), 30.0, right_lane_open=False)
  assert confirm(controller, fork_state(320), 30.0, right_lane_open=True) == 1
  assert controller.update(fork_state(300), 30.0, right_lane_open=True,
                           lane_change_finished=True) == 0
  assert controller.update(fork_state(250), 30.0, right_lane_open=True) == 0


def test_bsd_style_block_can_retry_while_event_is_armed():
  controller = AtcForkLaneChangeController()
  confirm(controller, fork_state(340), 30.0, right_lane_open=False)
  # The controller keeps requesting while the regular lane-change FSM waits
  # for BSD to clear.
  assert confirm(controller, fork_state(320), 30.0, right_lane_open=True) == 1
  assert controller.update(fork_state(300), 30.0, right_lane_open=True) == 1


def test_new_exit_resets_completed_latch():
  controller = AtcForkLaneChangeController()
  confirm(controller, fork_state(340), 30.0, right_lane_open=False)
  controller.update(fork_state(320), 30.0, right_lane_open=True, lane_change_finished=True)
  assert confirm(controller, fork_state(340, turn_type=43), 30.0, right_lane_open=False) == 0
  assert confirm(controller, fork_state(300, turn_type=43), 30.0, right_lane_open=True) == 1


def test_single_frame_lane_open_noise_does_not_request():
  controller = AtcForkLaneChangeController()
  confirm(controller, fork_state(340), 30.0, right_lane_open=False)
  assert controller.update(fork_state(320), 30.0, right_lane_open=True) == 0
  assert controller.update(fork_state(319), 30.0, right_lane_open=False) == 0


def test_does_not_arm_before_speed_based_action_range():
  controller = AtcForkLaneChangeController()
  # At 100 km/h (27.8 m/s), the action range is about 333 m.
  confirm(controller, fork_state(400), 27.8, right_lane_open=False)
  assert confirm(controller, fork_state(320), 27.8, right_lane_open=True) == 0

