"""LateralAutoTune on a simulated car: the factor converges to the car's, friction follows lag."""
import math

from selfdrive.controls.lib.lateral_autotune import (DT, EVAL_S, FACTOR_BOUNDS, LateralAutoTune)


def drive_curves(tuner, true_factor, seconds, roll_bias=0.0):
  """Alternating steady left / right curves; feedback makes up whatever the feedforward misses."""
  t = 0.0
  while t < seconds:
    phase = (t % 20.0)
    side = 1.0 if phase < 10.0 else -1.0
    ramp = min(1.0, (phase % 10.0) / 2.0)          # 2 s into each curve, then steady
    desired = side * 1.2 * ramp
    needed = desired / true_factor + roll_bias      # torque that really holds the curve
    f = desired / tuner.factor                      # what the feedforward gives with the tune
    feedback = needed - f
    tuner.update(True, 25.0, False, False, False, desired, desired, f, feedback, 0.0, 0.0, 0.3)
    t += DT


def test_factor_converges_to_the_car():
  tuner = LateralAutoTune(2.75, 0.10)
  drive_curves(tuner, 2.4, 30 * 60)
  assert abs(tuner.factor - 2.4) < 0.05, tuner.status()


def test_factor_cancels_road_crown():
  tuner = LateralAutoTune(2.75, 0.10)
  drive_curves(tuner, 2.75, 20 * 60, roll_bias=0.03)
  assert abs(tuner.factor - 2.75) < 0.08, tuner.status()


def test_factor_is_bounded_and_slow():
  tuner = LateralAutoTune(2.75, 0.10)
  drive_curves(tuner, 1.5, 60 * 60)                  # far outside the tune
  assert FACTOR_BOUNDS[0] * 2.75 - 1e-9 <= tuner.factor < FACTOR_BOUNDS[0] * 2.75 + 0.01
  tuner = LateralAutoTune(2.75, 0.10)
  drive_curves(tuner, 1.5, EVAL_S + 1)               # one evaluation moves at most 2 %
  assert tuner.factor >= 2.75 * 0.98 - 1e-9


def test_nothing_learned_without_clean_samples():
  tuner = LateralAutoTune(2.75, 0.10)
  for k in range(int(10 * 60 / DT)):
    d = 1.2 * math.sin(k * DT)
    tuner.update(True, 25.0, True, False, False, d, d, d / 2.75, 0.5, 0.0, 0.0, 0.3)   # driver steering
    tuner.update(True, 8.0, False, False, False, d, d, d / 2.75, 0.5, 0.0, 0.0, 0.3)   # too slow
  assert tuner.factor == 2.75 and tuner.friction == 0.10


def small_signal(tuner, lag_s, seconds, delay=0.3):
  hist = []
  t = 0.0
  while t < seconds:
    desired = 0.4 * math.sin(2 * math.pi * 0.25 * t)
    hist.append(desired)
    n = int(round((delay + lag_s) / DT))
    actual = hist[-1 - n] if len(hist) > n else 0.0
    # SteerActuatorDelay setting = total delay - the controller's 0.2 s plan lead
    tuner.update(True, 25.0, False, False, False, desired, actual, desired / tuner.factor, 0.0, 0.0, 0.0, delay - 0.2)
    t += DT


def test_friction_rises_when_late_and_falls_when_early():
  late = LateralAutoTune(2.75, 0.10)
  small_signal(late, 0.15, 5 * 60)
  assert late.friction > 0.10
  early = LateralAutoTune(2.75, 0.10)
  small_signal(early, -0.15, 5 * 60)
  assert early.friction < 0.10
  right = LateralAutoTune(2.75, 0.10)
  small_signal(right, 0.0, 5 * 60)
  assert right.friction == 0.10


def test_delay_is_reported():
  tuner = LateralAutoTune(2.75, 0.10)
  hist = []
  for k in range(int(3 * 60 / DT)):
    desired = 1.0 * math.sin(2 * math.pi * 0.2 * k * DT)
    hist.append(desired)
    actual = hist[-36] if len(hist) > 35 else 0.0       # 0.35 s
    tuner.update(True, 25.0, False, False, False, desired, actual, 0.0, 0.0, 0.0, 0.0, 0.3)
  assert abs(tuner.status()["delay"] - 0.35) < 0.03


def test_delay_measures_every_window_and_carries_over():
  tuner = LateralAutoTune(2.75, 0.10)
  hist = []
  for k in range(int(60 / DT)):
    desired = 1.0 * math.sin(2 * math.pi * 0.2 * k * DT)
    hist.append(desired)
    actual = hist[-36] if len(hist) > 35 else 0.0
    tuner.update(True, 25.0, False, False, False, desired, actual, 0.0, 0.0, 0.0, 0.0, 0.3)
  assert tuner.delay_windows == 6                      # one per 10 s, not one per 30 s evaluation
  st = tuner.status()
  again = LateralAutoTune(2.75, 0.10, delay_estimate=st["delay"], delay_windows=st["delay_windows"])
  assert again.delay_windows == 6 and abs(again.status()["delay"] - 0.35) < 0.03
  assert LateralAutoTune(2.75, 0.10, delay_estimate=None, delay_windows=7).delay_windows == 0


def test_restores_saved_values_within_bounds():
  assert LateralAutoTune(2.75, 0.10, 2.6, 0.12).factor == 2.6
  assert LateralAutoTune(2.75, 0.10, 9.0, 9.0).factor == 2.75 * FACTOR_BOUNDS[1]


def test_steer_delay_applied_gradually():
  from selfdrive.controls.lib.lateral_autotune import DELAY_MIN_WINDOWS
  tuner = LateralAutoTune(2.75, 0.10, sad=0.30)
  hist = []
  k = 0
  while tuner.delay_windows < DELAY_MIN_WINDOWS + 3:
    desired = 1.0 * math.sin(2 * math.pi * 0.2 * k * DT)
    hist.append(desired)
    actual = hist[-46] if len(hist) > 45 else 0.0       # 0.45 s total -> SAD 0.25 s
    tuner.update(True, 25.0, False, False, False, desired, actual, 0.0, 0.0, 0.0, 0.0, 0.3)
    k += 1
  assert 0.25 - 1e-6 <= tuner.sad < 0.30
  assert tuner.sad >= 0.30 - 0.01 * 4 - 1e-6           # 0.01 s per evaluation


def test_lane_auto_center_moves_toward_the_middle():
  import numpy as np
  from selfdrive.controls.lib.lane_auto_center import BOUNDS, LaneAutoCenter
  x = np.linspace(0, 50, 33)
  c = LaneAutoCenter(0.0)
  car_right_of_centre = 0.15                           # lane centre 15 cm to the left
  for _ in range(int(60 * 60 / 0.05)):
    shift = car_right_of_centre - c.offset             # the offset moves the car left
    c.update(True, 25.0, 0.0, x, np.full(33, 1.75 + shift), np.full(33, -1.75 + shift), 0.9, 0.9)
  assert abs(c.offset - 0.15) < 0.02
  c = LaneAutoCenter(0.0)
  for _ in range(int(60 * 60 / 0.05)):                 # curves / bad lines: nothing learned
    c.update(True, 25.0, 0.002, x, np.full(33, 2.0), np.full(33, -1.5), 0.9, 0.9)
    c.update(True, 25.0, 0.0, x, np.full(33, 2.0), np.full(33, -1.5), 0.3, 0.9)
  assert c.offset == 0.0
  assert LaneAutoCenter(1.0).offset == BOUNDS[1]


def test_factor_learned_sticks_after_the_halving():
  tuner = LateralAutoTune(2.75, 0.10)
  assert not tuner.status()["factor_learned"]
  drive_curves(tuner, 2.6, 5 * 60)
  assert tuner.status()["factor_learned"]
  assert LateralAutoTune(2.75, 0.10, 2.7, 0.1, None, True).status()["factor_learned"]


def test_steady_friction_compensation_does_not_raise_the_factor():
  """2026-11-28: a small steady error keeps friction compensation on through the curve. It is part of f
  but not of the lateral-accel term; the factor must still settle on the car's, not ~15 % above it."""
  tuner = LateralAutoTune(2.75, 0.085)
  t = 0.0
  while t < 30 * 60:
    phase = t % 20.0
    side = 1.0 if phase < 10.0 else -1.0
    desired = side * 1.5 * min(1.0, (phase % 10.0) / 2.0)
    needed = desired / 2.75
    f = desired / tuner.factor + math.copysign(0.08, desired)   # friction comp on all curve long
    tuner.update(True, 25.0, False, False, False, desired, desired - 0.1 * side, f, needed - f, 0.0, 0.0, 0.3)
    t += DT
  assert abs(tuner.factor - 2.75) < 0.05, tuner.status()


def test_kf_scales_the_implied_factor():
  tuner = LateralAutoTune(2.75, 0.10, kf=1.2)
  t = 0.0
  while t < 30 * 60:
    phase = t % 20.0
    desired = (1.0 if phase < 10.0 else -1.0) * 1.2 * min(1.0, (phase % 10.0) / 2.0)
    needed = 1.2 * desired / 2.6                     # output units: kf * lateral term
    f = 1.2 * desired / tuner.factor
    tuner.update(True, 25.0, False, False, False, desired, desired, f, needed - f, 0.0, 0.0, 0.3)
    t += DT
  assert abs(tuner.factor - 2.6) < 0.05, tuner.status()


def test_factor_target_shows_where_it_is_heading():
  tuner = LateralAutoTune(2.75, 0.10)
  assert tuner.status()["factor_target"] is None
  drive_curves(tuner, 2.5, 25)
  assert abs(tuner.status()["factor_target"] - 2.5) < 0.02
