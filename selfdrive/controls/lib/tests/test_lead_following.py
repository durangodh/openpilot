from types import SimpleNamespace as NS

import pytest

from selfdrive.controls.lib.lead_following import (get_follow_obstacle_cost, get_follow_approach_limit,
                                                 get_closing_lead_accel_limit, get_traffic_accel_limit)


def lead(v_ego=20.0, **kwargs):
  values = dict(status=True, dRel=80.0, vLead=v_ego, aLeadK=0.0)
  values.update(kwargs)
  return NS(**values)


def cost(v_ego=20.0, **kwargs):
  values = dict(base_cost=6.0, v_ego=v_ego, a_ego=0.2, planned_accel=0.2,
                leads=(lead(v_ego), NS(status=False)), t_follow=1.45,
                stop_distance=6.0, comfort_brake=2.5)
  values.update(kwargs)
  return get_follow_obstacle_cost(**values)


@pytest.mark.parametrize('kph,expected', [(0, 6.0), (18, 6.0), (30, 6.0), (60, 4.8), (100, 3.9), (120, 3.9)])
def test_gap_recovery_changes_only_above_departure_window(kph, expected):
  assert cost(v_ego=kph / 3.6) == pytest.approx(expected)


@pytest.mark.parametrize('kwargs', [dict(vLead=0), dict(vLead=3.0), dict(vLead=19.5),
                                  dict(aLeadK=-0.2), dict(dRel=34.0), dict(dRel=float('nan'))])
def test_braking_closing_or_short_gap_restores_original_cost(kwargs):
  assert cost(leads=(lead(**kwargs),)) == 6.0


def test_second_lead_can_cancel_comfort():
  assert cost(leads=(lead(), lead(dRel=20.0, vLead=0.0))) == 6.0
  assert cost(leads=(NS(status=False),)) == 6.0


def test_actual_or_planned_braking_restores_original_cost():
  assert cost(a_ego=-0.01) == 6.0
  assert cost(planned_accel=-0.01) == 6.0


@pytest.mark.parametrize('field', ['a_ego', 'planned_accel'])
@pytest.mark.parametrize('kph', [60.0, 100.0])
def test_acceleration_zero_crossing_does_not_toggle_following_weight(field, kph):
  values = [cost(v_ego=kph / 3.6, **{field: accel})
            for accel in [-0.001, 0.0, 0.001, 0.0, -0.001]]
  assert max(values) - min(values) < 0.001
  assert values[0] == values[1] == 6.0


@pytest.mark.parametrize('field', ['a_ego', 'planned_accel'])
def test_comfort_returns_progressively_before_acceleration_reaches_zero(field):
  values = [cost(**{field: accel}) for accel in [0.2, 0.15, 0.1, 0.05, 0.0, -0.1]]
  assert all(a < b for a, b in zip(values[:4], values[1:5]))
  assert values[-2:] == [6.0, 6.0]


def test_user_cost_and_gap_settings_are_respected():
  assert cost(base_cost=2.0) == 2.0
  assert cost(base_cost=12.0) < 12.0
  assert cost(t_follow=4.0) == 6.0
  assert cost(leads=(lead(dRel=40.0),), stop_distance=15.0) == 6.0


def test_comfort_fades_before_gap_deficit_or_closing_gate():
  ordinary = cost()
  near_gap = cost(leads=(lead(dRel=36.0),))
  closing = cost(leads=(lead(vLead=19.75),))
  assert ordinary < near_gap < 6.0
  assert ordinary < closing < 6.0


@pytest.mark.parametrize('distance,expected', [(70.0, 1.0), (66.0, 1.0), (62.0, 0.5), (58.0, 0.0)])
def test_approach_removes_only_positive_allowance(distance, expected):
  cap, comfortable = get_follow_approach_limit(1.0, 20.0, (lead(vLead=18.0, dRel=distance),), 50.0)
  assert comfortable
  assert cap == pytest.approx(expected)


@pytest.mark.parametrize('kwargs', [dict(vLead=0.0), dict(vLead=17.5), dict(aLeadK=-0.35),
                                  dict(dRel=40.0), dict(dRel=float('nan'))])
def test_approach_comfort_rejects_hazardous_or_invalid_secondary_lead(kwargs):
  leads = (lead(vLead=18.0, dRel=60.0), lead(**kwargs))
  assert get_follow_approach_limit(1.0, 20.0, leads, 50.0) == (1.0, False)


def test_approach_preserves_low_speed_and_invalid_target_gap():
  assert get_follow_approach_limit(1.0, 5.0, (lead(),), 6.0) == (1.0, False)
  assert get_follow_approach_limit(1.0, 20.0, (lead(),), float('nan')) == (1.0, False)


@pytest.mark.parametrize('speed,distance', [(0.0, 77.6), (7.8, 41.8), (10.37, 5.8)])
def test_closing_or_stopped_lead_does_not_keep_positive_allowance(speed, distance):
  assert get_closing_lead_accel_limit(1.6, 13.3, (lead(vLead=speed, dRel=distance),), 30.0) == 0.0


def test_closing_throttle_cap_is_progressive_and_checks_second_lead():
  caps = [get_closing_lead_accel_limit(1.0, 20.0, (lead(vLead=18.0, dRel=d),), 50.0)
          for d in (70.0, 66.0, 62.0, 58.0)]
  assert caps == pytest.approx([1.0, 1.0, 0.5, 0.0])
  assert get_closing_lead_accel_limit(1.0, 20.0, (lead(vLead=22.0), lead(vLead=0.0)), 50.0) == 0.0


def test_closing_cap_preserves_departure_and_negative_requests():
  assert get_closing_lead_accel_limit(1.6, 2.0, (lead(vLead=3.0, dRel=7.0),), 6.0) == 1.6
  assert get_closing_lead_accel_limit(-2.0, 20.0, (lead(vLead=0.0),), 50.0) == -2.0
  assert get_closing_lead_accel_limit(1.0, 20.0, (lead(),), float('nan')) == 1.0


def test_throttle_lifts_before_speed_difference_when_lead_slows():
  steady = lead(vLead=20.0, dRel=52.0)
  slowing = lead(vLead=20.0, dRel=52.0, aLeadK=-0.5)
  assert get_closing_lead_accel_limit(1.0, 20.0, (steady,), 50.0) == 1.0
  assert get_closing_lead_accel_limit(1.0, 20.0, (slowing,), 50.0) < 0.6
  assert get_closing_lead_accel_limit(1.0, 20.0, (steady,), 50.0, a_ego=0.5) < 0.6
  # Equal acceleration/pulling away should not be mistaken for closure.
  steady.aLeadK = 0.5
  assert get_closing_lead_accel_limit(1.0, 20.0, (steady,), 50.0, a_ego=0.5) == 1.0


def test_preview_ignores_small_acceleration_noise_and_low_speed_entry_is_continuous():
  for accel in (-0.1, 0.0, 0.1):
    assert get_closing_lead_accel_limit(1.0, 20.0,
                                      (lead(vLead=20.0, dRel=52.0, aLeadK=accel),), 50.0) == 1.0
  stopped = lead(vLead=0.0, dRel=10.0)
  caps = [get_closing_lead_accel_limit(1.0, speed, (stopped,), 6.0)
          for speed in (0.49, 0.5, 0.51, 1.75, 2.99, 3.0, 3.01)]
  assert caps[:2] == [1.0, 1.0]
  assert 0.99 < caps[2] <= 1.0
  assert caps[3:] == pytest.approx([0.5, 0.004, 0.0, 0.0])
  assert all(a >= b for a, b in zip(caps, caps[1:]))


def test_traffic_launch_tracks_lead_without_large_gap_recovery_surge():
  brisk = lead(vLead=2.0, aLeadK=1.6, dRel=7.0)
  gentle = lead(vLead=1.0, aLeadK=0.4, dRel=7.0)
  assert get_traffic_accel_limit(2.0, 0.0, brisk, 6.0) == 2.0  # release is not slowed
  assert get_traffic_accel_limit(2.0, 1.0, brisk, 6.0) == pytest.approx(1.4)
  assert get_traffic_accel_limit(2.0, 1.0, gentle, 6.0) == pytest.approx(0.75)
  # A lower configured cap is respected; this never commands acceleration.
  assert get_traffic_accel_limit(0.4, 1.0, brisk, 6.0) == pytest.approx(0.4)
  assert get_traffic_accel_limit(-2.0, 1.0, brisk, 6.0) == -2.0


def test_traffic_catch_up_is_bounded_and_fades_at_road_speed():
  gentle = lead(vLead=2.0, aLeadK=0.4, dRel=20.0)
  assert get_traffic_accel_limit(2.0, 3.0, gentle, 6.0) == pytest.approx(1.0)
  caps = [get_traffic_accel_limit(2.0, speed, gentle, 6.0)
          for speed in (5.0, 6.0, 7.0, 30.0 / 3.6)]
  assert caps[0] == 1.0 and caps[-1] == 2.0
  assert all(a < b for a, b in zip(caps, caps[1:]))
  assert get_traffic_accel_limit(2.0, 0.0, NS(status=False), 6.0) == 2.0
  assert get_traffic_accel_limit(2.0, 0.0, lead(dRel=float('nan')), 6.0) == 2.0


def test_traffic_restop_lifts_throttle_but_departing_lead_is_not_delayed():
  stopped = lead(vLead=0.0, aLeadK=0.0, dRel=7.0)
  assert get_closing_lead_accel_limit(1.4, 3.0, (stopped,), 6.0) == 0.0
  departing = lead(vLead=3.0, aLeadK=1.0, dRel=7.0)
  cap = get_traffic_accel_limit(2.0, 1.0, departing, 6.0)
  assert cap == pytest.approx(1.35)
  assert get_closing_lead_accel_limit(cap, 1.0, (departing,), 6.0, a_ego=0.5) == cap
