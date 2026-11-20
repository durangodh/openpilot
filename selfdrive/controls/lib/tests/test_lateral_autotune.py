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
    tuner.update(True, 25.0, False, False, False, desired, actual, desired / tuner.factor, 0.0, 0.0, 0.0, delay)
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


def test_restores_saved_values_within_bounds():
  assert LateralAutoTune(2.75, 0.10, 2.6, 0.12).factor == 2.6
  assert LateralAutoTune(2.75, 0.10, 9.0, 9.0).factor == 2.75 * FACTOR_BOUNDS[1]
