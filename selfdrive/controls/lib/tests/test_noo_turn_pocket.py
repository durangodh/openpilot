"""carrot-rust turn-pocket behaviour through the production DesireHelper FSM."""
import unittest
from types import SimpleNamespace as NS

from selfdrive.controls.lib.navigation_noo import NavigationLaneChangeController
from selfdrive.controls.lib.navigation_route import NavigationRouteData
from selfdrive.controls.lib.tests.test_noo_lane_change_fsm import (
  FakeNoo, FakeRoute, car, OFF, PRE, STARTING)
from selfdrive.controls.lib.tests.test_steering_input_regressions import load_planner_module


class TestTurnPocket(unittest.TestCase):
  def setUp(self):
    module = load_planner_module("desire_helper")
    self.h = module.DesireHelper()
    h = self.h
    h.last_params_update = float("inf")
    h.lane_change_enabled = True
    h.noo_enabled = h.noo_turn_pocket = True
    h.noo_mode = 0
    h.lane_change_need_torque = 0
    h.noo_lane_wait = 0.0
    h.noo_controller = FakeNoo()
    self.state = dict(h.empty_navigation_state, fresh=True, kind="turn",
                      direction=-1, distance=100.0, road_limit_kph=50.0)
    h.navigation_route = FakeRoute(self.state)
    h.noo_pose_trust = NS(update=lambda *args: True, reset=lambda: None)
    h._road_edge_blocked = lambda *args: False

  def step(self, speed, count=2, torque=0.0, bsd=False):
    c = car(speed)
    c.steeringPressed, c.steeringTorque = torque != 0, torque
    c.leftBlindspot = c.rightBlindspot = bsd
    for _ in range(count):
      self.h.update(c, True, 0.5)
    return self.h.lane_change_state

  def test_closed_turn_lane_keeps_preparation_at_standstill_and_at_speed(self):
    self.h._road_edge_blocked = lambda *args: True
    self.assertEqual(self.step(0, 40), PRE)
    self.assertEqual(self.step(60, 40, torque=1), PRE)
    self.assertEqual(self.h.desire, 0)

  def test_preparation_alone_never_moves_even_with_visible_adjacent_lane(self):
    self.assertEqual(self.step(20, 80, torque=1), PRE)
    self.assertEqual(self.h.noo_reason, "pocket_wait")

  def test_wait_survives_deceleration_from_40_to_zero(self):
    self.h.noo_controller.direction = -1
    self.assertEqual(self.step(40), PRE)  # left still needs a nudge
    self.assertEqual(self.step(20), PRE)
    self.assertEqual(self.step(0), PRE)

  def test_left_pocket_requires_a_nudge_and_respects_signal_delay(self):
    self.h.noo_controller.direction = -1
    self.h.noo_lane_wait = 1.0
    self.assertEqual(self.step(20, 30), PRE)
    self.assertEqual(self.step(20, torque=1), STARTING)
    self.setUp()
    self.h.noo_controller.direction = -1
    self.h.noo_lane_wait = 1.0
    self.assertEqual(self.step(20, 10, torque=1), PRE)

  def test_right_pocket_starts_only_at_10_kph(self):
    self.state["direction"] = self.h.noo_controller.direction = 1
    self.assertEqual(self.step(9.9, 40), PRE)
    self.assertEqual(self.step(10), STARTING)

  def test_low_speed_start_needs_an_actual_adjacent_lane(self):
    self.state["direction"] = self.h.noo_controller.direction = 1
    self.h.navigation_route.camera_lane_estimate = lambda _: ({"count": 3, "current": 2}, "")
    self.assertEqual(self.step(20, 40), PRE)

  def test_bsd_blocks_even_after_a_previous_driver_override(self):
    self.h.noo_controller.direction = -1
    self.h.prev_torque_applied = True
    self.assertEqual(self.step(20, 60, torque=1, bsd=True), PRE)

  def test_fork_next_turn_and_disabled_option_keep_the_normal_speed_gate(self):
    for change in ({"kind": "fork"}, {"fresh": False}, {"distance": 500.0}):
      self.setUp()
      self.h.noo_controller.direction = -1
      self.state.update(change)
      self.assertEqual(self.step(20, torque=1), OFF)
    self.setUp()
    self.h.noo_turn_pocket = False
    self.h.noo_controller.direction = -1
    self.assertEqual(self.step(20, torque=1), OFF)

  def test_expired_guidance_brake_and_driver_cancel_release_preparation(self):
    for stop in ("stale", "brake", "canceled", "opposite"):
      self.setUp()
      self.assertEqual(self.step(0), PRE)
      c = car(0)
      if stop == "stale":
        self.state["fresh"] = False
      elif stop == "brake":
        c.brakePressed = True
      elif stop == "canceled":
        self.h.noo_controller.canceled = True
      else:
        c.steeringPressed, c.steeringTorque = True, -1.0
      self.h.update(c, True, 0.0)
      self.assertEqual(self.h.lane_change_state, OFF, stop)

  def test_turn_boundary_hands_off_from_preparation_to_turn_desire(self):
    self.h.navigation_route.steering_request = NavigationRouteData.steering_request
    self.assertEqual(self.step(20), PRE)
    self.state["distance"] = 45.0
    self.step(20)
    self.assertEqual(self.h.noo_turn_direction, -1)
    self.assertEqual(self.h.desire, 1)  # test schema's turnLeft

  def test_wide_road_turn_replaces_a_pending_lane_request_at_60_metres(self):
    self.state["next_road_width"] = 10.0
    self.h.navigation_route.steering_request = NavigationRouteData.steering_request
    self.h.noo_controller.direction = -1
    self.assertEqual(self.step(20), PRE)
    self.state["distance"] = 55.0
    self.step(20, torque=1)
    self.assertEqual(self.h.noo_direction, 0)
    self.assertEqual(self.h.noo_turn_direction, -1)
    self.assertNotEqual(self.h.lane_change_state, STARTING)

  def test_lost_pose_clears_an_already_latched_turn_immediately(self):
    self.h.turn_state, self.h.turn_direction_latched = 1, -1
    self.h.noo_pose_trust = NS(update=lambda *args: False, reset=lambda: None)
    self.step(20)
    self.assertEqual(self.h.turn_state, 0)
    self.assertEqual(self.h.noo_turn_direction, 0)

  def test_preparation_uses_the_same_width_boundary_as_turn_steering(self):
    check = NavigationLaneChangeController.turn_pocket_direction
    for width, boundary in ((5, 43), (10, 60)):
      s = dict(self.state, next_road_width=width, distance=boundary)
      self.assertEqual(check(s, 5), 0)
      self.assertEqual(check(dict(s, distance=boundary + 1), 5), -1)
    for kind in ("fork", "uturn", "rotary"):
      self.assertEqual(check(dict(self.state, kind=kind), 5), 0)


if __name__ == "__main__":
  unittest.main()
