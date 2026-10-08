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
                 ADJUST_OFFSET_LIMIT=.4, valid_lane_path=valid_lane_path,
                 valid_samples=valid_samples, lane_horizon_weights=lane_horizon_weights,
                 curve_centering_weight=curve_centering_weight,
                 DT_MDL=.05, CURVE_CENTER_DELAY_BP=[1.0, 3.0])
exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
             'lane_planner.py', 'exec'), namespace)


class Filter:
  def __init__(self):
    self.x = 0.0

  def update(self, value):
    self.x += .05 / 2.05 * (value - self.x)


def scenario(curvature, width=3.7, adjust=.1, advance=.01, confidence=.99):
  t = np.linspace(0, 3.2, 33)
  x = 15 * t
  center = .5 * curvature * x ** 2
  lp = NS(param_read_frame=5, lat_mpc_input_offset=advance,
          curve_time=0.0, curve_sign=0.0, curve_center_weight=0.0,
          adjust_lane_offset=adjust, laneless_offset=0, ll_t=t.copy(), ll_x=x,
          lll_y=center - width / 2, rll_y=center + width / 2,
          lll_prob=confidence, rll_prob=confidence, lll_std=.1, rll_std=.1,
          frame=0, lane_width=3.5, readings=[], lane_width_left=3.0, lane_width_right=1.0,
          lane_width_left_filtered=Filter(), lane_width_right_filtered=Filter(),
          lane_offset_filtered=Filter())
  path = np.column_stack([x, center, np.zeros(33)])
  speed = np.sign(curvature) * 80 if curvature else 200
  return lp, t, path, speed


@pytest.mark.parametrize('curvature', [-.01, .01])
@pytest.mark.parametrize('width', [2.3, 3.5, 3.7])
def test_short_and_long_curves_follow_midpoint_without_inside_offset(curvature, width):
  lp, t, path, speed = scenario(curvature, width=width)
  before = path.copy()
  # The saved input lead is kept on bends; only the inward offset is gone.
  expected = np.interp(np.minimum(t * 1.01, t[-1]), t, path[:, 1])
  for frame in range(600):  # width learning and a 30-second sustained bend
    out = namespace['get_d_path'](lp, 15, t, path, 1.0, speed)
    if frame >= 60:  # lane centre is fully applied after 3 s of the same bend
      np.testing.assert_allclose(out[:, 1], expected, atol=1e-8)
    lp.param_read_frame = 5  # keep the supplied runtime Params for this fixture
  np.testing.assert_array_equal(path, before)
  assert lp.lat_mpc_input_offset == .01


def test_straight_retains_saved_input_lead_and_space_offset():
  lp, t, path, speed = scenario(0)
  lp.adjust_lane_offset = 0
  lp.lll_y += .2 * t
  lp.rll_y += .2 * t
  lp.lane_width = 3.7
  out = namespace['get_d_path'](lp, 15, t, path, 1.0, speed)
  assert out[10, 1] == pytest.approx(.2 * t[10] * 1.01)
  lp.adjust_lane_offset = .1
  for _ in range(200):
    lp.param_read_frame = 5
    out = namespace['get_d_path'](lp, 15, t, path, 1.0, speed)
  assert lp.lane_offset > .09


def test_unreliable_lanes_keep_model_fallback():
  lp, t, path, speed = scenario(.01, confidence=0)
  model = path.copy()
  model[:, 1] += .3
  out = namespace['get_d_path'](lp, 15, t, model, 1.0, speed)
  np.testing.assert_allclose(out, model)


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


def test_laneless_uses_centre_only_on_confident_bends():
  w = curve_centering_weight
  assert curve_lane_center_blend(.15, w(80), .95, .1, 3.7) == 1.0
  assert curve_lane_center_blend(.15, w(-80), .95, .1, 3.7) == 1.0
  assert curve_lane_center_blend(.15, w(200), .95, .1, 3.7) == .15
  for prob, std, width in [(.4, .1, 3.7), (.95, .3, 3.7), (.95, .1, 6.0)]:
    assert curve_lane_center_blend(.15, w(80), prob, std, width) == .15
  assert .15 < curve_lane_center_blend(.15, w(175), .95, .1, 3.7) < 1.0


def test_lane_centre_waits_for_a_sustained_bend():
  # Short bends keep the model line (laneless) and the legacy lane path; the
  # centre takes over only once the same bend lasts 1-3 s.
  lp, t, path, speed = scenario(.01, advance=0.0)
  weights = []
  for _ in range(80):  # 4 s at 20 Hz
    lp.param_read_frame = 5
    namespace['get_d_path'](lp, 15, t, path, 1.0, speed)
    weights.append(lp.curve_center_weight)
  assert weights[10] == 0.0                     # 0.5 s
  assert weights[39] == pytest.approx(.5, abs=.03)  # 2 s
  assert weights[-1] == 1.0                     # 4 s
  w = curve_centering_weight
  assert curve_lane_center_blend(.15, weights[10], .95, .1, 3.7) == .15
  assert curve_lane_center_blend(.15, weights[-1], .95, .1, 3.7) == 1.0
  lp.param_read_frame = 5
  namespace['get_d_path'](lp, 15, t, path, 1.0, -speed)  # S-bend direction change
  assert lp.curve_center_weight == 0.0
  lp.param_read_frame = 5
  namespace['get_d_path'](lp, 15, t, path, 1.0, 200)  # straight
  assert lp.curve_time == 0.0 and w(200) == 0.0
