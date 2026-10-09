import numpy as np

from selfdrive.controls.lib.lane_center_hold import BLEND_TIME, HOLD_BLEND, HOLD_TIME, LaneCenterHold

DT = 0.05
X = np.linspace(0.0, 60.0, 33)


def drive(hold, steps, lanes_lost=True, enabled=True, curvature=0.0, model_y=-0.5):
  out = None
  for _ in range(steps):
    hold.update(enabled, False, 12.0, curvature, X, None, DT)
    model = np.column_stack([X, np.full(33, model_y), np.zeros(33)])
    out = hold.apply(model, lanes_lost)
  return out


def remembered(centre=0.3):
  hold = LaneCenterHold()
  hold.update(True, True, 12.0, 0.0, X, np.full(33, centre), DT)
  return hold


def test_held_centre_pulls_a_drifting_model_path_back():
  hold = remembered()
  out = drive(hold, int(BLEND_TIME / DT) + 2)
  assert hold.blend == HOLD_BLEND
  assert out[5, 1] == np.float64(-0.5 * (1 - HOLD_BLEND) + 0.3 * HOLD_BLEND)


def test_blend_ramps_in_and_releases_after_hold_time():
  hold = remembered()
  drive(hold, 1)
  assert 0.0 < hold.blend < HOLD_BLEND
  out = drive(hold, int((HOLD_TIME + BLEND_TIME) / DT) + 2)
  assert hold.blend == 0.0
  np.testing.assert_allclose(out[:, 1], -0.5)


def test_blinker_or_curve_releases_smoothly():
  for kwargs in (dict(enabled=False), dict(curvature=0.01)):
    hold = remembered()
    drive(hold, int(BLEND_TIME / DT) + 2)
    drive(hold, 1, **kwargs)
    assert 0.0 < hold.blend < HOLD_BLEND
    drive(hold, int(BLEND_TIME / DT) + 1, **kwargs)
    assert hold.blend == 0.0


def test_visible_lanes_do_not_apply_hold():
  hold = remembered()
  out = drive(hold, 20, lanes_lost=False)
  assert hold.blend == 0.0
  np.testing.assert_allclose(out[:, 1], -0.5)
