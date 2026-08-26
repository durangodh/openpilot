import pytest

from selfdrive.controls.lib.longitudinal_mpc_lib.long_mpc import (COMFORT_BRAKE, STOP_DISTANCE, T_FOLLOW,
                                                                 desired_follow_distance,
                                                                 get_lead_departure_cost_multiplier,
                                                                 get_safe_obstacle_distance,
                                                                 get_stopped_equivalence_factor)


def test_lead_departure_assist_requires_a_real_departing_lead():
  assert get_lead_departure_cost_multiplier(0.0, 2.0, 0.5, False) == 1.0
  assert get_lead_departure_cost_multiplier(2.0, 2.2, 0.5, True) == 1.0


def test_lead_departure_assist_reacts_early_at_low_speed():
  multiplier = get_lead_departure_cost_multiplier(0.0, 1.5, 0.0, True)
  assert multiplier == pytest.approx(0.35)


def test_lead_departure_assist_fades_with_ego_speed():
  low_speed = get_lead_departure_cost_multiplier(2.0, 3.5, 0.0, True)
  higher_speed = get_lead_departure_cost_multiplier(10.0, 11.5, 0.0, True)
  assert 0.35 < low_speed < higher_speed < 1.0
  assert get_lead_departure_cost_multiplier(12.0, 14.0, 0.0, True) == 1.0


def test_lead_braking_immediately_disables_departure_assist():
  assert get_lead_departure_cost_multiplier(2.0, 4.0, -0.2, True) == 1.0
  assert get_lead_departure_cost_multiplier(2.0, 4.0, -1.0, True) == 1.0


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
