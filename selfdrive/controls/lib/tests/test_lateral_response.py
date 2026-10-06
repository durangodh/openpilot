import math

import pytest

from selfdrive.controls.lib.lateral_response import LateralResponse


def sequence(preview, speed=7.0, curvature=0.0, cost=550.0):
  response = LateralResponse()
  return [response.update(cost, y, curvature, speed, True, 0.05) for y in preview]


@pytest.mark.parametrize('direction', [-1, 1])
def test_persistent_turn_and_lane_shift_relax_cost_within_three_model_intervals(direction):
  costs = sequence([direction * 0.4] * 10)
  assert costs[0] == 550.0
  assert costs[3] == pytest.approx(467.5)
  assert all(467.5 <= c <= 550 for c in costs)
  assert all(a >= b for a, b in zip(costs, costs[1:]))


def test_already_tracking_curve_keeps_normal_cost():
  speed, curvature = 7.0, 0.015
  preview = 0.5 * curvature * (speed * 0.8) ** 2
  assert sequence([preview] * 20, speed, curvature) == [550.0] * 20


@pytest.mark.parametrize('preview', [[0.06] * 20, [0.4, -0.4] * 10,
                                    [0.0] * 5 + [0.5] + [0.0] * 14])
def test_straight_offset_alternating_noise_and_single_jump_keep_normal_cost(preview):
  assert sequence(preview) == [550.0] * len(preview)


@pytest.mark.parametrize('speed', [0.0, 1.5, 22.2, 30.0])
def test_no_adjustment_at_standstill_or_highway_speed(speed):
  assert sequence([0.5] * 20, speed) == [550.0] * 20


def test_existing_user_cost_floor_is_preserved():
  assert sequence([0.5] * 20, cost=200.0) == [200.0] * 20
  assert min(sequence([0.5] * 20, cost=1200.0)) == pytest.approx(1020.0)


def test_release_returns_to_normal_cost_without_overshoot():
  costs = sequence([0.4] * 10 + [0.0] * 12)
  release = costs[10:]
  assert all(a <= b for a, b in zip(release, release[1:]))
  assert release[-1] == 550.0
  assert max(b - a for a, b in zip(release, release[1:])) <= 8.25 + 1e-9


@pytest.mark.parametrize('active,preview', [(False, 0.4), (True, math.nan)])
def test_driver_override_or_invalid_reference_resets_history(active, preview):
  response = LateralResponse()
  for _ in range(10):
    response.update(550, 0.4, 0, 7, True, 0.05)
  assert response.update(550, preview, 0, 7, active, 0.05) == 550
  assert response.update(550, 0.4, 0, 7, True, 0.05) == 550
