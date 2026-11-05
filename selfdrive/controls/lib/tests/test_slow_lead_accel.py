"""Stop-and-go: no extra closing speed toward a slow, near lead."""
from types import SimpleNamespace as NS

import pytest

from selfdrive.controls.lib.longitudinal_limits import limit_accel_behind_slow_lead


def lead(v, d=15.0, a=0.0, status=True):
  return NS(status=status, vLead=v, dRel=d, aLeadK=a)


def test_catching_up_to_a_slow_lead_stops_adding_closing_speed():
  # Ego 6 m/s behind a lead at 3 m/s: closing 3 m/s, past the 2.5 m/s end.
  assert limit_accel_behind_slow_lead(1.5, 6.0, lead(3.0)) == pytest.approx(0.2)
  # Half way through the closing range.
  assert limit_accel_behind_slow_lead(1.5, 4.5, lead(3.0)) == pytest.approx(0.85)


def test_following_a_departing_lead_is_unchanged():
  assert limit_accel_behind_slow_lead(1.5, 2.0, lead(3.0)) == 1.5      # ego slower
  assert limit_accel_behind_slow_lead(1.5, 3.4, lead(3.0)) == 1.5      # within 0.5 m/s
  # Lead still accelerating hard: allow at least its acceleration.
  assert limit_accel_behind_slow_lead(1.5, 6.0, lead(3.0, a=1.0)) == pytest.approx(1.0)


def test_only_slow_and_near_leads():
  assert limit_accel_behind_slow_lead(1.5, 12.0, lead(9.0)) == 1.5      # lead above 30 km/h
  assert limit_accel_behind_slow_lead(1.5, 6.0, lead(3.0, d=45.0)) == 1.5
  assert limit_accel_behind_slow_lead(1.5, 6.0, lead(3.0, status=False)) == 1.5
  assert limit_accel_behind_slow_lead(1.5, 6.0, None) == 1.5


def test_never_raises_a_lower_cap():
  assert limit_accel_behind_slow_lead(0.1, 6.0, lead(3.0, a=1.0)) == 0.1
