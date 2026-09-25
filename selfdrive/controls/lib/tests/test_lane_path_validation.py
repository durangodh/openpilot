import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS
from unittest.mock import patch

import numpy as np
import pytest

from selfdrive.controls.lib.lane_path_validation import valid_lane_path, covers_lane_horizon


@pytest.fixture
def lane_planner():
  # Load the real planner with only hardware/native imports replaced. Numerical
  # filtering, interpolation and the path-building algorithm are exercised intact.
  class Params:
    def get(self, key, encoding=None):
      return {'LanelessOffset': '10'}.get(key)
  params = ModuleType('common.params')
  params.Params = Params
  realtime = ModuleType('common.realtime')
  realtime.DT_MDL = 0.05
  swaglog = ModuleType('selfdrive.swaglog')
  swaglog.cloudlog = NS(warning=lambda *args: None)
  cereal = ModuleType('cereal')
  cereal.log = NS(LateralPlan=NS(Desire=NS(laneChangeLeft=3, laneChangeRight=4)))
  file = Path(__file__).parents[1] / 'lane_planner.py'
  spec = importlib.util.spec_from_file_location('lane_planner_under_test', file)
  module = importlib.util.module_from_spec(spec)
  with patch.dict(sys.modules, {'common.params': params, 'common.realtime': realtime,
                               'selfdrive.swaglog': swaglog, 'cereal': cereal}):
    spec.loader.exec_module(module)
  return module.LanePlanner()


def model():
  t, x = np.linspace(0, 10, 33), np.linspace(0, 200, 33)
  lines = [NS(t=t.copy(), x=x.copy(), y=np.full(33, y)) for y in (-5.5, -1.8, 1.8, 5.5)]
  edges = [NS(t=t.copy(), y=np.full(33, y)) for y in (-4.0, 4.0)]
  return NS(laneLines=lines, laneLineProbs=[0.9] * 4, laneLineStds=[0.1] * 4,
            roadEdges=edges, roadEdgeStds=[0.1, 0.1], meta=NS(desireState=[0, 0, 0, 0.3, 0.4]))


def path():
  result = np.zeros((33, 3))
  result[:, 0] = np.linspace(0, 200, 33)
  return result


@pytest.mark.parametrize('bad', ['missing', 'short_y', 'nan_x', 'infinite_y', 'time_reverse', 'x_duplicate', 'bad_prob', 'bad_std'])
def test_bad_frame_clears_previous_lane_confidence(lane_planner, bad):
  lp = lane_planner
  lp.parse_model(model())
  assert lp.lll_prob > 0
  md = model()
  if bad == 'missing':
    md.laneLines = []
  elif bad == 'short_y':
    md.laneLines[2].y = md.laneLines[2].y[:-1]
  elif bad == 'nan_x':
    md.laneLines[1].x[8] = np.nan
  elif bad == 'infinite_y':
    md.laneLines[2].y[4] = np.inf
  elif bad == 'time_reverse':
    md.laneLines[1].t[8] = md.laneLines[1].t[7]
  elif bad == 'x_duplicate':
    md.laneLines[2].x[2] = md.laneLines[2].x[1]
  elif bad == 'bad_prob':
    md.laneLineProbs[2] = np.nan
  else:
    md.laneLineStds[1] = -1.0
  lp.parse_model(md)
  assert lp.lll_prob == lp.rll_prob == 0
  original = path()
  result = lp.get_d_path(20, np.linspace(0, 10, 33), original, 1.0)
  assert np.isfinite(result).all()
  assert np.allclose(result[:, 1], 0.1)  # model path plus current laneless offset
  assert np.all(original[:, 1] == 0)
  assert lp.d_prob == 0


def test_short_horizon_falls_back_without_endpoint_extrapolation(lane_planner):
  md = model()
  for line in md.laneLines:
    line.t *= 0.1
  lane_planner.parse_model(md)
  result = lane_planner.get_d_path(20, np.linspace(0, 10, 33), path(), 1)
  assert result[0, 1] == pytest.approx(lane_planner.camera_offset)
  assert np.allclose(result[4:, 1], 0.1)
  assert lane_planner.camera_offset < result[1, 1] < 0.1


def test_good_frame_recovers_after_bad_frame(lane_planner):
  lane_planner.parse_model(NS())
  lane_planner.parse_model(model())
  assert lane_planner.lll_prob == 0.9
  result = lane_planner.get_d_path(20, np.linspace(0, 10, 33), path(), 1)
  assert lane_planner.d_prob == 1
  assert np.allclose(result[:, 1], lane_planner.camera_offset)


def test_missing_edges_clear_old_asymmetric_space(lane_planner):
  md = model()
  md.roadEdges[0].y[:] = -8
  lane_planner.parse_model(md)
  lane_planner.get_d_path(20, np.linspace(0, 10, 33), path(), 1)
  assert lane_planner.lane_width_left_filtered.x > 0
  md.roadEdges = []
  lane_planner.parse_model(md)
  assert lane_planner.lane_width_left_filtered.x == lane_planner.lane_width_right_filtered.x == 0
  assert lane_planner.lll_prob > 0  # invalid edges do not discard otherwise valid lanes


def test_short_desire_array_does_not_reuse_old_lane_change_probability(lane_planner):
  lane_planner.parse_model(model())
  md = model()
  md.meta.desireState = [0]
  lane_planner.parse_model(md)
  assert lane_planner.l_lane_change_prob == lane_planner.r_lane_change_prob == 0


def test_stopped_path_distance_need_not_increase_for_lane_time_interpolation(lane_planner):
  lane_planner.parse_model(model())
  result = lane_planner.get_d_path(0, np.linspace(0, 10, 33), np.zeros((33, 3)), 1)
  assert np.isfinite(result).all()
  assert lane_planner.d_prob == 1


def test_query_horizon_and_numerical_validation():
  t = np.linspace(0, 10, 33)
  assert valid_lane_path(t, t * 20, t * 0 - 1.8, t * 0 + 1.8)
  assert covers_lane_horizon(t, t)
  assert not covers_lane_horizon(t, t * 1.04)
  assert not covers_lane_horizon(t * 0.9, t)  # final MPC samples also need coverage
  assert not covers_lane_horizon(t * 0.2, t)
  assert not covers_lane_horizon(t, t[::-1])
  assert not valid_lane_path(t, np.zeros(33), t, t)


def test_c2_normal_nan_padded_times_keep_lane_control(lane_planner):
  md = model()
  t = np.full(33, np.nan)
  t[:6] = [0, 1, 2, 4, 7, 10]
  for line in md.laneLines:
    line.t = t.copy()
  for edge in md.roadEdges:
    edge.t = t.copy()
  lane_planner.parse_model(md)
  assert lane_planner.lll_prob == 0.9
  result = lane_planner.get_d_path(2, np.linspace(0, 10, 33), path(), 1)
  assert np.allclose(result[:, 1], lane_planner.camera_offset)
  assert lane_planner.d_prob == 1


def test_internal_nan_time_hole_is_rejected(lane_planner):
  md = model()
  md.laneLines[1].t[5] = np.nan
  lane_planner.parse_model(md)
  assert lane_planner.lll_prob == 0
