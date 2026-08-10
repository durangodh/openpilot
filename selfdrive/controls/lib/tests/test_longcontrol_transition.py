import pytest

from selfdrive.controls.lib.longitudinal_transition import (ACCEL_TRANSITION_JERK_UP,
                                                           limit_accel_increase)


DT = 0.01


def test_transition_limits_only_accel_increase():
  output, remaining = limit_accel_increase(0.7, -0.6, 2.0, DT)
  assert output == pytest.approx(-0.6 + ACCEL_TRANSITION_JERK_UP * DT)
  assert remaining == pytest.approx(2.0 - DT)

  output, remaining = limit_accel_increase(-1.2, -0.6, 2.0, DT)
  assert output == -1.2
  assert remaining == pytest.approx(2.0 - DT)


def test_transition_expires_and_releases_target():
  output, remaining = limit_accel_increase(0.7, -0.6, 0.0, DT)
  assert output == 0.7
  assert remaining == 0.0
