import pytest
from types import SimpleNamespace

from selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import (COMFORT_BRAKE, STOP_DISTANCE, T_FOLLOW,
                                                                 desired_follow_distance,
                                                                 LongitudinalMpc,
                                                                 get_safe_obstacle_distance,
                                                                 get_stopped_equivalence_factor)


def cost_multipliers(v_ego, lead0, lead1, t_follow=1.45, depart_cost=0.05):
  # Exercise the real cost policy without constructing the native MPC solver.
  mpc = SimpleNamespace(x0=[0.0, v_ego, 0.0], t_follow=t_follow,
                        lead_depart_cost=depart_cost)
  return LongitudinalMpc.get_cost_multipliers(mpc, lead0, lead1)


def test_departure_cost_requires_both_leads_to_be_at_least_as_fast():
  assert cost_multipliers(2.0, 1.0, 4.0) == pytest.approx((1.0, 1.0, 1.0))
  assert cost_multipliers(2.0, 4.0, 1.0) == pytest.approx((1.0, 1.0, 1.0))


def test_departure_cost_uses_c2_default_and_fades_by_36_kph():
  assert cost_multipliers(0.0, 1.5, 1.5) == pytest.approx((0.05, 0.05, 1.0))
  assert cost_multipliers(5.0, 7.0, 7.0) == pytest.approx((0.525, 0.525, 1.0))
  assert cost_multipliers(10.0, 12.0, 12.0) == pytest.approx((1.0, 1.0, 1.0))


def test_departure_cost_respects_configured_value():
  assert cost_multipliers(0.0, 1.0, 1.0, depart_cost=0.2) == pytest.approx((0.2, 0.2, 1.0))


def test_gap_cost_still_applies_without_departure_assistance():
  assert cost_multipliers(10.0, 8.0, 8.0, t_follow=1.2) == pytest.approx((0.8, 0.8, 1.3))


def test_desired_follow_distance_keeps_legacy_signature():
  speed = 10.0
  assert desired_follow_distance(speed, speed) == pytest.approx(T_FOLLOW * speed + STOP_DISTANCE)
  assert desired_follow_distance(speed, speed, 1.2) == pytest.approx(1.2 * speed + STOP_DISTANCE)


def test_desired_follow_distance_matches_dynamic_mpc_model():
  v_ego, v_lead, t_follow, stop_dist = 5.0, 7.0, 1.3, 6.0
  expected = max(0.0,
                 get_safe_obstacle_distance(v_ego, t_follow, stop_dist, COMFORT_BRAKE) -
                 get_stopped_equivalence_factor(v_lead, v_ego, t_follow, stop_dist,
                                                krkeegan=True, comfort_brake=COMFORT_BRAKE))
  assert desired_follow_distance(v_ego, v_lead, t_follow, stop_dist,
                                 COMFORT_BRAKE, krkeegan=True) == pytest.approx(expected)


def test_fast_departing_lead_does_not_publish_negative_distance():
  assert desired_follow_distance(0.0, 30.0) == 0.0
