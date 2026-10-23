import ast
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from selfdrive.controls.lib.vision_curve_speed import VisionCurveSpeed, curve_speed_target, UNLIMITED_SPEED


def model(curve_at=0, curvature=0.02, speed=20.0):
  t = np.linspace(0.0, 10.0, 33)
  yaw = np.zeros(33)
  yaw[curve_at:] = speed * curvature
  return NS(position=NS(x=t * speed, y=np.zeros(33), t=t),
            velocity=NS(x=np.full(33, speed)), orientationRate=NS(z=yaw))


def target(md, **kwargs):
  args = dict(factor=1.0, minimum_speed=5 / 3.6, approach_decel=1.2)
  args.update(kwargs)
  return curve_speed_target(md, **args)


def test_near_and_far_identical_curves_have_different_approach_caps():
  near, far = model(), model(curve_at=16)
  assert target(near) == pytest.approx(np.sqrt(1.9 / 0.02))
  assert target(far) == pytest.approx(np.sqrt(1.9 / 0.02 + 2 * 1.2 * 100))
  assert target(far) > target(near)


def test_turning_geometry_is_independent_of_model_speed():
  assert target(model(speed=5)) == pytest.approx(target(model(speed=25)))


def test_each_sample_uses_its_own_predicted_speed():
  md = model()
  md.velocity.x = np.linspace(20, 5, 33)
  md.orientationRate.z = md.velocity.x * 0.02
  assert target(md) == pytest.approx(np.sqrt(1.9 / 0.02))


def test_factor_minimum_and_approach_settings_remain_effective():
  assert target(model(), factor=2) < target(model(), factor=1)
  assert target(model(), minimum_speed=15) == 15
  assert target(model(curve_at=16), approach_decel=0.3) < target(model(curve_at=16), approach_decel=1.2)


@pytest.mark.parametrize('field', ['x', 'y', 't', 'speed', 'yaw'])
@pytest.mark.parametrize('bad', ['short', 'nan', 'inf'])
def test_invalid_arrays_are_rejected(field, bad):
  md = model()
  owner, name = (md.velocity, 'x') if field == 'speed' else ((md.orientationRate, 'z') if field == 'yaw' else (md.position, field))
  value = getattr(owner, name).copy()
  if bad == 'short':
    value = value[:-1]
  else:
    value[5] = float(bad)
  setattr(owner, name, value)
  assert target(md) is None


def test_reversed_time_and_negative_speed_are_rejected():
  md = model()
  md.position.t[4] = md.position.t[3]
  assert target(md) is None
  md = model()
  md.velocity.x[3] = -1
  assert target(md) is None


def test_stopped_or_straight_model_does_not_request_curve_braking():
  assert target(model(speed=0)) == UNLIMITED_SPEED
  assert target(model(curvature=0)) == UNLIMITED_SPEED


def test_restrict_immediately_but_hold_then_ramp_release():
  control = VisionCurveSpeed()
  def update(md):
    return control.update(md, 1, 5 / 3.6, 1.2, 0.2)
  first = update(model())
  assert first == pytest.approx(target(model()))
  assert update(model(curvature=0)) == first
  assert update(model(curvature=0)) == pytest.approx(first + 0.2)
  assert update(model(curvature=0.04)) == pytest.approx(target(model(curvature=0.04)))


def test_dropouts_hold_then_release_and_valid_detection_can_restrict_again():
  control = VisionCurveSpeed()
  first = control.update(model(), 1, 5 / 3.6, 1.2, 0.2)
  assert control.update(None, 1, 5 / 3.6, 1.2, 0.2) == first
  assert control.update(None, 1, 5 / 3.6, 1.2, 0.2) == first
  for _ in range(10):
    control.update(None, 1, 5 / 3.6, 1.2, 0.2)
  assert first < control.target < first + 3
  assert control.update(model(), 1, 5 / 3.6, 1.2, 0.2) == first
  control.reset()
  assert control.target == UNLIMITED_SPEED


def test_cruise_helper_wiring_cadence_units_and_invalid_messages():
  source = Path(__file__).parents[1] / 'cruise_helper.py'
  tree = ast.parse(source.read_text())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CruiseHelper')
  method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'cal_curve_speed')
  env = {'DT_CTRL': 0.01, 'CV': NS(KPH_TO_MS=1 / 3.6)}
  exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), env)
  helper = NS(curve_update_frame=None, vision_curve_speed=VisionCurveSpeed(),
              curve_speed_ms=UNLIMITED_SPEED, auto_curve_speed_factor=1.2,
              auto_curve_speed_lower_limit=30, auto_curve_speed_decel_rate=1.2)
  class Messages(dict):
    valid = {'modelV2': True}
    alive = {'modelV2': True}
  sm = Messages(modelV2=model())
  env['cal_curve_speed'](helper, sm, 20, 1)
  assert helper.curve_speed_ms == UNLIMITED_SPEED
  env['cal_curve_speed'](helper, sm, 20, 20)
  expected = target(sm['modelV2'], factor=1.2, minimum_speed=30 / 3.6)
  assert helper.curve_speed_ms == pytest.approx(expected)
  sm.valid = {'modelV2': False}
  sm['modelV2'] = model(curvature=0.2)
  env['cal_curve_speed'](helper, sm, 20, 40)
  assert helper.curve_speed_ms == pytest.approx(expected)
