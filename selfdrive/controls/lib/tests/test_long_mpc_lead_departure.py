import pytest
from types import SimpleNamespace

from selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import (COMFORT_BRAKE, STOP_DISTANCE, T_FOLLOW,
                                                                 desired_follow_distance,
                                                                 LongitudinalMpc,
                                                                 get_safe_obstacle_distance,
                                                                 get_stopped_equivalence_factor)


def cost_multipliers(v_ego, lead0, lead1, t_follow=1.45, depart_cost=0.05,
                     a_lead0=0.0, lead0_status=True):
  # Exercise the real cost policy without constructing the native MPC solver.
  mpc = SimpleNamespace(x0=[0.0, v_ego, 0.0], t_follow=t_follow,
                        lead_depart_cost=depart_cost)
  return LongitudinalMpc.get_cost_multipliers(
    mpc, lead0, lead1, a_lead0=a_lead0, lead0_status=lead0_status)


def test_departure_cost_requires_confirmed_pulling_away_lead():
  assert cost_multipliers(2.0, 4.0, 4.0, lead0_status=False) == pytest.approx((1.0, 1.0, 1.0))
  assert cost_multipliers(2.0, 2.2, 4.0) == pytest.approx((1.0, 1.0, 1.0))
  assert cost_multipliers(2.0, 4.0, 4.0, a_lead0=-0.3) == pytest.approx((1.0, 1.0, 1.0))


def test_departure_cost_restores_smoothing_after_launch():
  assert cost_multipliers(0.0, 1.5, 1.5) == pytest.approx((0.05, 0.05, 1.0))
  assert cost_multipliers(2.5, 4.5, 4.5) == pytest.approx((0.3833333333, 0.3833333333, 1.0))
  assert cost_multipliers(5.0, 7.0, 7.0) == pytest.approx((0.55, 0.55, 1.0))
  assert cost_multipliers(20.0 / 3.6, 8.0, 8.0)[0] < 1.0
  assert cost_multipliers(30.0 / 3.6, 10.0, 10.0) == pytest.approx((1.0, 1.0, 1.0))


def test_departure_cost_respects_configured_value():
  assert cost_multipliers(0.0, 1.0, 1.0, depart_cost=0.2) == pytest.approx((0.2, 0.2, 1.0))


def test_gap_cost_holds_to_18_kph_then_fades_by_30_kph():
  assert cost_multipliers(0.0, 0.0, 0.0, t_follow=1.2) == pytest.approx((0.8, 0.8, 1.3))
  assert cost_multipliers(5.0, 5.0, 5.0, t_follow=1.2) == pytest.approx((0.8, 0.8, 1.3))
  assert cost_multipliers(24.0 / 3.6, 6.0, 6.0, t_follow=1.2) == pytest.approx((0.9, 0.9, 1.15))
  assert cost_multipliers(30.0 / 3.6, 8.0, 8.0, t_follow=1.2) == pytest.approx((1.0, 1.0, 1.0))
  assert cost_multipliers(10.0, 8.0, 8.0, t_follow=1.2) == pytest.approx((1.0, 1.0, 1.0))


def test_departing_lead_distance_boost_ends_at_30_kph():
  v_lead = 12.0
  normal = get_stopped_equivalence_factor(v_lead, 30.0 / 3.6, krkeegan=False)
  dynamic = get_stopped_equivalence_factor(v_lead, 30.0 / 3.6, krkeegan=True)
  assert dynamic == pytest.approx(normal)


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
