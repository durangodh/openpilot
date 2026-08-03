from selfdrive.controls.lib.carrot_navi_atc import AtcForkLaneChangeController


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

