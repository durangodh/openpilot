"""Exercise the actual lane path method without native cereal/Params bindings."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from selfdrive.controls.lib.lane_path_validation import (
  curve_centering_weight, curve_lane_center_blend, lane_horizon_weights,
  valid_lane_path, valid_samples,
)

source = ast.parse((Path(__file__).parents[1] / 'lane_planner.py').read_text())
method = next(node for cls in source.body if isinstance(cls, ast.ClassDef)
              for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'get_d_path')
namespace = dict(np=np, clip=np.clip, interp=np.interp, mean=np.mean,
                  ENABLE_ZORROBYTE=True, ENABLE_INC_LANE_PROB=True,
                  valid_lane_path=valid_lane_path,
                 valid_samples=valid_samples, lane_horizon_weights=lane_horizon_weights,
                 curve_centering_weight=curve_centering_weight,
                 DT_MDL=.05, CURVE_CENTER_DELAY_BP=[1.0, 3.0], D_PROB_BLEND_TIME=0.5)
exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
             'lane_planner.py', 'exec'), namespace)


class Filter:
  def __init__(self):
    self.x = 0.0

  def update(self, value):
    self.x += .05 / 2.05 * (value - self.x)


def scenario(curvature, width=3.7, confidence=.99):
  t = np.linspace(0, 3.2, 33)
  x = 15 * t
  center = .5 * curvature * x ** 2
  lp = NS(curve_time=0.0, curve_sign=0.0, curve_center_weight=0.0,
          d_prob=0.0, lane_change_fade=False, ll_t=t.copy(), ll_x=x,
          lll_y=center - width / 2, rll_y=center + width / 2,
          lll_prob=confidence, rll_prob=confidence, lll_std=.1, rll_std=.1,
          frame=0, lane_width=3.5, readings=[], lane_width_left=3.0, lane_width_right=1.0,
          lane_width_left_filtered=Filter(), lane_width_right_filtered=Filter())
  path = np.column_stack([x, center, np.zeros(33)])
  speed = np.sign(curvature) * 80 if curvature else 200
  return lp, t, path, speed


@pytest.mark.parametrize('curvature', [-.01, .01])
@pytest.mark.parametrize('width', [2.3, 3.5, 3.7])
def test_short_and_long_curves_follow_midpoint_without_inside_offset(curvature, width):
  lp, t, path, speed = scenario(curvature, width=width)
  before = path.copy()
  expected = path[:, 1]
  for frame in range(600):  # width learning and a 30-second sustained bend
    out = namespace['get_d_path'](lp, 15, t, path, 1.0, speed)
    if frame >= 60:  # lane centre is fully applied after 3 s of the same bend
      np.testing.assert_allclose(out[:, 1], expected, atol=1e-8)
  np.testing.assert_array_equal(path, before)


def test_straight_follows_lane_path_without_private_offsets():
  lp, t, path, speed = scenario(0)
  lp.lll_y += .2 * t
  lp.rll_y += .2 * t
  lp.lane_width = 3.7
  out = namespace['get_d_path'](lp, 15, t, path, 1.0, speed)
  assert out[10, 1] == pytest.approx(.2 * t[10])


def test_unreliable_lanes_keep_model_fallback():
  lp, t, path, speed = scenario(.01, confidence=0)
  model = path.copy()
  model[:, 1] += .3
  out = namespace['get_d_path'](lp, 15, t, model, 1.0, speed)
  np.testing.assert_allclose(out, model)


def test_lane_change_fade_does_not_step_d_prob():
  # Both lanes crossing 0.5 during the lane-change fade used to drop d_prob
  # 1.0 -> 0.45 in one frame (steering snap at lane-change start).
  lp, t, path, speed = scenario(0.0, width=3.4, confidence=.55)
  namespace['get_d_path'](lp, 15, t, path.copy(), 1.0, speed)
  assert lp.d_prob == 1.0
  lp.lll_prob = lp.rll_prob = .45
  lp.lane_change_fade = True
  namespace['get_d_path'](lp, 15, t, path.copy(), 1.0, speed)
  assert lp.d_prob == pytest.approx(.9)
  lp.lane_change_fade = False
  lp.d_prob = 1.0
  namespace['get_d_path'](lp, 15, t, path.copy(), 1.0, speed)
  assert lp.d_prob == pytest.approx(.45)


def test_nan_padded_lane_horizon_preserves_model_beyond_coverage():
  lp, t, path, speed = scenario(.01)
  lp.ll_t[20:] = np.nan
  model = path.copy()
  model[:, 1] += .3
  lp.curve_time = 3.0  # sustained bend: lane centre fully applied
  lp.curve_sign = np.sign(speed)
  out = namespace['get_d_path'](lp, 15, t, model, 1.0, speed)
  np.testing.assert_allclose(out[20:], model[20:])
  np.testing.assert_allclose(out[0, 1], path[0, 1])


def test_lane_centre_waits_for_a_sustained_bend():
  # In lane mode the centre takes over only once the same bend lasts 1-3 s.
  lp, t, path, speed = scenario(.01)
  weights = []
  for _ in range(80):  # 4 s at 20 Hz
    namespace['get_d_path'](lp, 15, t, path, 1.0, speed)
    weights.append(lp.curve_center_weight)
  assert weights[10] == 0.0                     # 0.5 s
  assert weights[39] == pytest.approx(.5, abs=.03)  # 2 s
  assert weights[-1] == 1.0                     # 4 s
  w = curve_centering_weight
  assert curve_lane_center_blend(.15, weights[10], .95, .1, 3.7) == .15
  assert curve_lane_center_blend(.15, weights[-1], .95, .1, 3.7) == 1.0
  namespace['get_d_path'](lp, 15, t, path, 1.0, -speed)  # S-bend direction change
  assert lp.curve_center_weight == 0.0
  namespace['get_d_path'](lp, 15, t, path, 1.0, 200)  # straight
  assert lp.curve_time == 0.0 and w(200) == 0.0
