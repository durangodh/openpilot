from types import SimpleNamespace as NS

import numpy as np

from selfdrive.modeld.constants import T_IDXS
from selfdrive.controls.lib.turn_accel import get_future_curvature, limit_accel_in_future_turns

T = np.asarray(T_IDXS)


def model(speed=15.0, curvature=0.0, start_t=0.0):
  yaw = np.where(T >= start_t, speed * curvature, 0.0)
  return NS(position=NS(x=T * speed, y=np.zeros(33), z=np.zeros(33)),
            velocity=NS(x=np.full(33, speed)), orientationRate=NS(z=yaw))


def limit(md, v=15.0, curvature=0.0, current=0.0, a_max=1.5, v_cruise=30.0):
  return limit_accel_in_future_turns(v, curvature, [-3.0, a_max], 3.0, model_msg=md,
                                     v_cruise=v_cruise, current_curvature=current)[1]


def test_straight_road_keeps_the_existing_limit():
  assert limit(model()) == 1.5


def test_upcoming_curve_reduces_acceleration_before_steering_starts():
  upcoming = model(curvature=0.003, start_t=1.5)
  assert limit(upcoming) < 1.5
  assert limit(model(curvature=0.006, start_t=1.5)) < limit(upcoming)


def test_curve_beyond_three_seconds_is_ignored():
  assert limit(model(curvature=0.02, start_t=6.0)) == 1.5


def test_never_raises_the_limit_and_braking_limit_is_untouched():
  out = limit_accel_in_future_turns(15.0, 0.05, [-3.0, 0.5], 3.0, model_msg=model(curvature=0.05))
  assert out[0] == -3.0 and 0.0 <= out[1] <= 0.5


def test_future_curvature_uses_model_yaw_rate_at_one_second():
  assert abs(get_future_curvature(model(curvature=0.01), 0.0) - 0.01) < 1e-9
