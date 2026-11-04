"""aPilot c2 dual actuator delay (LongControl delayed_targets)."""
import pytest

from selfdrive.controls.lib.drive_helpers import CONTROL_N
from selfdrive.controls.lib.longcontrol import delayed_targets
from selfdrive.modeld.constants import T_IDXS


def plan(v0, accel):
  return [v0 + accel * t for t in T_IDXS[:CONTROL_N]]


def single(speeds, delay):
  return delayed_targets(speeds, speeds[0], 0.0, 0.0, delay, delay)


def test_equal_bounds_match_the_single_delay_target():
  speeds = plan(15.0, -1.0)
  v, a = delayed_targets(speeds, 15.0, 0.0, 0.0, 0.5, 0.5)
  assert v == pytest.approx(14.5)
  assert a == pytest.approx(-2.0)


def test_longer_setting_brakes_earlier_but_does_not_add_acceleration():
  slowing, speeding = plan(15.0, -1.0), plan(15.0, 1.0)
  assert delayed_targets(slowing, 15.0, 0.0, 0.0, 0.8, 0.5) == pytest.approx(single(slowing, 0.8))
  assert delayed_targets(slowing, 15.0, 0.0, 0.0, 0.8, 0.5)[0] < single(slowing, 0.5)[0]
  assert delayed_targets(speeding, 15.0, 0.0, 0.0, 0.8, 0.5) == pytest.approx(single(speeding, 0.5))


def test_shorter_setting_softens_acceleration_but_not_braking():
  slowing, speeding = plan(15.0, -1.0), plan(15.0, 1.0)
  assert delayed_targets(slowing, 15.0, 0.0, 0.0, 0.3, 0.5) == pytest.approx(single(slowing, 0.5))
  assert delayed_targets(speeding, 15.0, 0.0, 0.0, 0.3, 0.5) == pytest.approx(single(speeding, 0.3))
  assert delayed_targets(speeding, 15.0, 0.0, 0.0, 0.3, 0.5)[0] < single(speeding, 0.5)[0]
