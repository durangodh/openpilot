from types import SimpleNamespace as NS

import pytest

from selfdrive.controls.lib.lead_following import get_follow_obstacle_cost


def lead(v_ego=20.0, **kwargs):
  values = dict(status=True, dRel=80.0, vLead=v_ego, aLeadK=0.0)
  values.update(kwargs)
  return NS(**values)


def cost(v_ego=20.0, **kwargs):
  values = dict(base_cost=6.0, v_ego=v_ego, a_ego=0.0, planned_accel=0.0,
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
