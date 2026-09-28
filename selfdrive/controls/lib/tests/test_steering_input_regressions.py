"""Exercise the steering input guards removed by the September 25 rollback.

Only native/hardware imports are replaced. The production lane interpolation,
filters, navigation reader and turn state machine run unchanged.
Run with: python -m unittest selfdrive.controls.lib.tests.test_steering_input_regressions
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace as NS
import unittest
from unittest.mock import patch

import numpy as np

from selfdrive.controls.lib.navigation_route import NavigationRouteData
from selfdrive.controls.lib.navigation_noo import NavigationLaneChangeController
from selfdrive.controls.lib.tests.test_navigation_noo import noo_state, confirm_noo


def load_planner_module(name):
  class Params:
    def get(self, key, encoding=None):
      return {"LanelessOffset": "10", "NooMode": "1"}.get(key)

    def get_bool(self, key):
      return key == "NavigationOnOpenpilot"

  params = ModuleType("common.params")
  params.Params = Params
  realtime = ModuleType("common.realtime")
  realtime.DT_MDL = 0.05
  swaglog = ModuleType("selfdrive.swaglog")
  swaglog.cloudlog = NS(warning=lambda *args: None)
  cereal = ModuleType("cereal")
  cereal.log = NS(LateralPlan=NS(
    Desire=NS(none=0, turnLeft=1, turnRight=2, laneChangeLeft=3,
              laneChangeRight=4, keepLeft=5, keepRight=6),
    LaneChangeState=NS(off=0, preLaneChange=1, laneChangeStarting=2, laneChangeFinishing=3),
    LaneChangeDirection=NS(none=0, left=1, right=2)))
  remote = ModuleType("selfdrive.eon_cluster.hud_remote")
  remote.RemoteLaneChangeSource = lambda: NS(poll=lambda: 0)
  source = Path(__file__).parents[1] / (name + ".py")
  spec = importlib.util.spec_from_file_location(name + "_under_test", source)
  module = importlib.util.module_from_spec(spec)
  with patch.dict(sys.modules, {
    "common.params": params, "common.realtime": realtime,
    "selfdrive.swaglog": swaglog, "cereal": cereal,
    "selfdrive.eon_cluster.hud_remote": remote,
  }):
    spec.loader.exec_module(module)
  return module


def model():
  t, x = np.linspace(0, 10, 33), np.linspace(0, 200, 33)
  lines = [NS(t=t.copy(), x=x.copy(), y=np.full(33, y))
           for y in (-5.4, -1.8, 1.8, 5.4)]
  edges = [NS(t=t.copy(), y=np.full(33, y)) for y in (-4.0, 4.0)]
  return NS(laneLines=lines, laneLineProbs=[0.9] * 4, laneLineStds=[0.1] * 4,
            roadEdges=edges, roadEdgeStds=[0.1, 0.1],
            meta=NS(desireState=[0, 0, 0, 0.3, 0.4]))


def path():
  result = np.zeros((33, 3))
  result[:, 0] = np.linspace(0, 25, 33)
  return result


class TestLaneInputRegression(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.planner_class = load_planner_module("lane_planner").LanePlanner

  def test_invalid_frame_does_not_reuse_previous_lane_path(self):
    for bad in ("missing", "short_y", "nan_x", "infinite_y", "time_reverse",
                "x_duplicate", "bad_prob", "bad_std", "time_hole"):
      with self.subTest(bad=bad):
        lp = self.planner_class()
        lp.parse_model(model())
        md = model()
        if bad == "missing":
          md.laneLines = []
        elif bad == "short_y":
          md.laneLines[2].y = md.laneLines[2].y[:-1]
        elif bad == "nan_x":
          md.laneLines[1].x[8] = np.nan
        elif bad == "infinite_y":
          md.laneLines[2].y[4] = np.inf
        elif bad == "time_reverse":
          md.laneLines[1].t[8] = md.laneLines[1].t[7]
        elif bad == "x_duplicate":
          md.laneLines[2].x[2] = md.laneLines[2].x[1]
        elif bad == "bad_prob":
          md.laneLineProbs[2] = np.nan
        elif bad == "bad_std":
          md.laneLineStds[1] = -1.0
        else:
          md.laneLines[1].t[5] = np.nan
        lp.parse_model(md)
        self.assertEqual((lp.lll_prob, lp.rll_prob), (0.0, 0.0))
        original = path()
        result = lp.get_d_path(2.5, np.linspace(0, 10, 33), original, 1.0)
        self.assertTrue(np.isfinite(result).all())
        np.testing.assert_allclose(result[:, 1], 0.1)
        np.testing.assert_array_equal(original[:, 1], 0.0)

  def test_short_lane_horizon_returns_to_model_path(self):
    lp = self.planner_class()
    md = model()
    for line in md.laneLines:
      line.t *= 0.1
    lp.parse_model(md)
    result = lp.get_d_path(2.5, np.linspace(0, 10, 33), path(), 1.0)
    self.assertAlmostEqual(result[0, 1], lp.camera_offset)
    np.testing.assert_allclose(result[4:, 1], 0.1)
    self.assertTrue(lp.camera_offset < result[1, 1] < 0.1)

  def test_normal_c2_nan_padding_keeps_valid_lane_control(self):
    lp = self.planner_class()
    md = model()
    times = np.full(33, np.nan)
    times[:6] = [0, 1, 2, 4, 7, 10]
    for line in md.laneLines + md.roadEdges:
      line.t = times.copy()
    lp.parse_model(md)
    result = lp.get_d_path(2.5, np.linspace(0, 10, 33), path(), 1.0)
    self.assertEqual(lp.d_prob, 1.0)
    np.testing.assert_allclose(result[:, 1], lp.camera_offset)

  def test_missing_edges_clear_previous_asymmetric_space(self):
    lp = self.planner_class()
    md = model()
    md.roadEdges[0].y[:] = -8
    lp.parse_model(md)
    lp.get_d_path(2.5, np.linspace(0, 10, 33), path(), 1.0)
    md.roadEdges = []
    lp.parse_model(md)
    self.assertEqual(lp.lane_width_left_filtered.x, 0.0)
    self.assertEqual(lp.lane_width_right_filtered.x, 0.0)
    self.assertGreater(lp.lll_prob, 0.0)

  def test_missing_desire_does_not_retain_lane_change_probability(self):
    lp = self.planner_class()
    lp.parse_model(model())
    md = model()
    md.meta.desireState = [0]
    lp.parse_model(md)
    self.assertEqual((lp.l_lane_change_prob, lp.r_lane_change_prob), (0.0, 0.0))

  def test_valid_frame_recovers_after_missing_model_fields(self):
    lp = self.planner_class()
    lp.parse_model(NS())
    lp.parse_model(model())
    result = lp.get_d_path(2.5, np.linspace(0, 10, 33), path(), 1.0)
    self.assertEqual(lp.d_prob, 1.0)
    np.testing.assert_allclose(result[:, 1], lp.camera_offset)


class TestTurnInputRegression(unittest.TestCase):
  def test_expired_guidance_releases_latched_turn(self):
    module = load_planner_module("desire_helper")
    clock = [100.0]
    car = NS(vEgo=2.5, brakePressed=False, steeringPressed=False,
             steeringTorque=0.0, steeringAngleDeg=0.0,
             leftBlinker=False, rightBlinker=False,
             leftBlindspot=False, rightBlindspot=False)
    with tempfile.TemporaryDirectory() as temp_dir:
      guide = Path(temp_dir) / "guide.json"
      guide.write_text(json.dumps({"updated_at_ms": 100000,
                                  "guidance_current": {"turn_type": 12, "distance_m": 30}}))
      with patch("time.time", side_effect=lambda: clock[0]), \
           patch("time.monotonic", side_effect=lambda: clock[0]):
        helper = module.DesireHelper()
        helper.navigation_route = NavigationRouteData(str(guide))
        helper.update(car, True, 0.0)
        self.assertEqual(helper.noo_turn_direction, -1)
        clock[0] = 103.1
        helper.update(car, True, 0.0)
        self.assertEqual(helper.turn_state, 0)
        self.assertEqual(helper.noo_turn_direction, 0)
        self.assertEqual(helper.desire, 0)


class TestNavigationInputRegression(unittest.TestCase):
  def test_fork_without_lane_guidance_does_not_guess_a_lane_change(self):
    ego = {"count": 3, "current": 2, "confidence": 0.9}
    for distance in (50.0, 100.0, 175.0, 300.0):
      with self.subTest(distance=distance):
        state = noo_state([0, 0, 1], distance=distance, road_limit=100.0)
        state.update(lane_fresh=False, lane_current=None)
        self.assertEqual(confirm_noo(NavigationLaneChangeController(), state, ego), 0)

  def test_current_fork_takes_priority_over_next_maneuver(self):
    state = noo_state([0, 1, 1], distance=180.0)
    state["lane_ahead_fresh"] = True
    state["lane_ahead"] = {"count": 3, "available": [0, 0, 1], "distance_m": 420.0}
    state["next"] = {"fresh": True, "direction": 1, "distance": 420.0, "turn_type": 43}
    ego = {"count": 3, "current": 2, "confidence": 0.9}
    controller = NavigationLaneChangeController()
    self.assertEqual(confirm_noo(controller, state, ego), 0)
    self.assertEqual(controller.target_lane, 2)
    state["lane_current"]["available"] = [0, 0, 1]
    self.assertEqual(confirm_noo(controller, state, ego), 1)

  def test_missing_plan_resets_continuous_open_confirmation(self):
    state = noo_state([0, 0, 1])
    ego = {"count": 3, "current": 2, "confidence": 0.9}
    controller = NavigationLaneChangeController()
    for _ in range(controller.CONFIRM_FRAMES - 1):
      self.assertEqual(controller.update(state, ego, 25.0, True, True), 0)
    self.assertEqual(controller.update({}, ego, 25.0, True, True), 0)
    self.assertEqual(controller.update(state, ego, 25.0, True, True), 0)
    self.assertEqual(confirm_noo(controller, state, ego), 1)


if __name__ == "__main__":
  unittest.main()
