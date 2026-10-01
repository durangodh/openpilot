"""NOO virtual-blinker handoff into the DesireHelper lane change FSM."""
from types import SimpleNamespace as NS
import unittest

from selfdrive.controls.lib.tests.test_steering_input_regressions import load_planner_module

OFF, PRE, STARTING, FINISHING = 0, 1, 2, 3
NONE, LEFT = 0, 1


class FakeNoo:
  def __init__(self):
    self.direction = 0
    self.requested_direction = 0
    self.current_lane = 2
    self.target_lane = 1

  def lane_plan(self, *args, **kwargs):
    return {"direction": self.direction}

  def update(self, *args, **kwargs):
    self.requested_direction = self.direction
    return self.direction

  def reset(self):
    pass


class FakeRoute:
  def __init__(self, empty):
    self.empty = empty

  def update(self):
    return self.empty

  def camera_lane_position(self, model_data):
    return {"count": 3, "current": 2, "left_adjacent": True, "right_adjacent": True}

  def steering_request(self, state, v_ego):
    return 0


def car(v_kph):
  return NS(vEgo=v_kph / 3.6, brakePressed=False, steeringPressed=False,
            steeringTorque=0.0, steeringAngleDeg=0.0,
            leftBlinker=False, rightBlinker=False,
            leftBlindspot=False, rightBlindspot=False)


class TestNooLaneChangeFsm(unittest.TestCase):
  def setUp(self):
    module = load_planner_module("desire_helper")
    helper = module.DesireHelper()
    helper.last_params_update = float("inf")
    helper.lane_change_enabled = True
    helper.auto_lane_change_enabled = False
    helper.lane_change_need_torque = 0
    helper.noo_enabled = True
    helper.noo_mode = 2
    helper.lane_change_speed_min = 50 / 3.6
    helper.auto_lane_change_timer_setting = 0
    helper.noo_controller = FakeNoo()
    helper.navigation_route = FakeRoute(helper.empty_navigation_state)
    self.edge = {-1: False, 1: False}
    helper._road_edge_blocked = lambda model_data, direction: self.edge[direction]
    self.helper = helper

  def test_request_raised_below_speed_starts_once_speed_gate_opens(self):
    self.helper.noo_controller.direction = -1
    self.helper.update(car(40), True, 0.0)
    self.assertEqual(self.helper.lane_change_state, OFF)
    self.helper.update(car(55), True, 0.0)
    self.assertEqual(self.helper.lane_change_state, PRE)
    self.helper.update(car(55), True, 0.0)
    self.assertEqual(self.helper.lane_change_state, STARTING)

  def test_adjacent_line_override_also_opens_the_fsm_edge_gate(self):
    # The raw road edge reads blocked, but the NOO adjacent-lane override opens it.
    self.edge[-1] = True
    self.helper.noo_controller.direction = -1
    self.helper.update(car(60), True, 0.0)
    self.helper.update(car(60), True, 0.0)
    self.assertFalse(self.helper.road_edge)
    self.assertEqual(self.helper.lane_change_state, STARTING)
    self.assertEqual(self.helper.lane_change_direction, LEFT)

  def test_edge_block_keeps_direction_and_never_starts_without_one(self):
    c = car(60)
    c.leftBlinker = True
    self.helper.auto_lane_change_timer_setting = 1
    self.edge[-1] = True
    self.helper.update(c, True, 0.0)
    self.helper.update(c, True, 0.0)
    self.assertEqual(self.helper.lane_change_state, PRE)
    self.assertEqual(self.helper.lane_change_direction, LEFT)

    self.helper.lane_change_direction = NONE
    self.edge[-1] = False
    for _ in range(40):
      self.helper.update(c, True, 0.0)
    self.assertEqual(self.helper.lane_change_state, PRE)


class FakeRemote:
  def __init__(self):
    self.direction = 0

  def poll(self):
    return self.direction


class TestRemoteLaneLatch(TestNooLaneChangeFsm):
  def setUp(self):
    super().setUp()
    self.helper.noo_controller.direction = 0
    self.remote = FakeRemote()
    self.helper.remote_lane = self.remote

  def press(self, c, direction=-1):
    self.remote.direction = direction
    for _ in range(6):   # 0.3 s pulse at 20 Hz
      self.helper.update(c, True, 0.0)
    self.remote.direction = 0

  def test_single_press_holds_through_the_lane_change(self):
    c = car(60)
    self.press(c)
    self.assertEqual(self.helper.lane_change_state, STARTING)
    for _ in range(10):
      self.helper.update(c, True, 0.5)
    self.assertEqual(self.helper.lane_change_state, STARTING)
    self.assertEqual(self.helper.remote_direction, -1)
    # Model reports the change done -> finishing -> released, back to off.
    for _ in range(40):
      self.helper.update(c, True, 0.0)
    self.assertEqual(self.helper.remote_direction, 0)
    self.assertEqual(self.helper.lane_change_state, OFF)

  def test_request_that_never_starts_times_out(self):
    c = car(60)
    c.leftBlindspot = True
    self.press(c)
    for _ in range(80):
      self.helper.update(c, True, 0.0)
    self.assertEqual(self.helper.remote_direction, 0)
    self.assertNotEqual(self.helper.lane_change_state, STARTING)

  def test_brake_cancels_the_latch(self):
    c = car(60)
    self.press(c)
    c.brakePressed = True
    self.helper.update(c, True, 0.5)
    self.assertEqual(self.helper.remote_direction, 0)


if __name__ == "__main__":
  unittest.main()
