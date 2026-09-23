from types import SimpleNamespace as NS

import pytest

from selfdrive.controls.lib.lead_departure import (LeadDepartureAssist,
                                                   departure_jerk_upper,
                                                   departure_motion_valid,
                                                   lead_is_departing)


def inputs():
  lead = NS(status=True, radar=True, dRel=7.0, vLeadK=0.8, vRel=0.8, aLeadK=0.2)
  return dict(enabled=True, stopping=True, confirmed=True,
              cs=NS(vEgo=0.0, brakePressed=False, gasPressed=False),
              plan=NS(onStop=False, fcw=False, trafficState=0, desiredDistance=6.0),
              radar=NS(leadOne=lead, leadTwo=NS(status=False), radarErrors=[]),
              radar_valid=True, plan_valid=True, plan_age=0.05,
              a_now=0.0, a_target=0.25, v_target=0.05, v_future=0.18, soft_hold=False)


def test_shared_departure_motion_predicate_rejects_boundaries_and_nonfinite_values():
  assert departure_motion_valid(0.26, 0.11)
  assert not departure_motion_valid(0.25, 0.11)
  assert not departure_motion_valid(0.26, 0.1)
  assert not departure_motion_valid(float('nan'), 0.2)
  lead = inputs()['radar'].leadOne
  assert lead_is_departing(lead)
  lead.vRel = float('inf')
  assert not lead_is_departing(lead)


def test_floor_never_exceeds_planner_and_distinguishes_creep():
  assist, args = LeadDepartureAssist(0.01), inputs()
  assert assist.update(**args)
  assert assist.accel_floor == 0.25
  args['a_target'] = 0.8
  assert assist.update(**args)
  assert assist.accel_floor == 0.35
  args['radar'].leadOne.vLeadK = 0.4
  assert assist.update(**args)
  assert assist.accel_floor == 0.18


@pytest.mark.parametrize('key,value', [
  ('enabled', False), ('confirmed', False), ('radar_valid', False),
  ('plan_valid', False), ('plan_age', 0.21), ('plan_age', -0.01),
  ('a_now', -0.01), ('a_target', 0.15), ('a_target', float('nan')),
  ('v_future', 0.05), ('soft_hold', True),
])
def test_rejected_requests_clear_all_assistance(key, value):
  assist, args = LeadDepartureAssist(0.01), inputs()
  assert assist.update(**args)
  args[key] = value
  assert not assist.update(**args)
  assert assist.remaining == assist.accel_floor == 0.0


@pytest.mark.parametrize('key,value', [
  ('status', False), ('radar', False), ('dRel', 3.0), ('dRel', 21.0),
  ('dRel', float('nan')), ('vLeadK', 0.25), ('vRel', 0.1),
  ('aLeadK', -0.01), ('aLeadK', float('inf')),
])
def test_lead_safety_veto(key, value):
  assist, args = LeadDepartureAssist(0.01), inputs()
  assert assist.update(**args)
  setattr(args['radar'].leadOne, key, value)
  assert not assist.update(**args)


@pytest.mark.parametrize('key,value', [('brakePressed', True), ('gasPressed', True), ('vEgo', 1.5)])
def test_driver_or_speed_veto(key, value):
  assist, args = LeadDepartureAssist(0.01), inputs()
  assert assist.update(**args)
  args['stopping'] = False
  setattr(args['cs'], key, value)
  assert not assist.update(**args)


@pytest.mark.parametrize('key,value', [('onStop', True), ('fcw', True), ('trafficState', 1),
                                     ('desiredDistance', float('nan'))])
def test_planner_stop_always_wins(key, value):
  assist, args = LeadDepartureAssist(0.01), inputs()
  assert assist.update(**args)
  args['stopping'] = False
  setattr(args['plan'], key, value)
  assert not assist.update(**args)


def test_nearer_stopped_second_lead_vetoes_departure():
  assist, args = LeadDepartureAssist(0.01), inputs()
  args['radar'].leadTwo = NS(status=True, radar=True, dRel=6.0, vLeadK=0.0, vRel=0.0, aLeadK=0.0)
  assert not assist.update(**args)


def test_hold_expires_without_rearming_in_pid_and_cannot_survive_braking():
  assist, args = LeadDepartureAssist(0.01), inputs()
  assert assist.update(**args)
  args.update(stopping=False, confirmed=False)
  for _ in range(99):
    assert assist.update(**args)
  for _ in range(20):
    assert not assist.update(**args)
  args.update(stopping=True, confirmed=True)
  assert assist.update(**args)
  args.update(stopping=False, a_now=-0.1)
  assert not assist.update(**args)
  args['a_now'] = 0.0
  assert not assist.update(**args)


def test_positive_plan_alone_cannot_arm_assist_while_driving():
  args = inputs()
  args['stopping'] = False
  assert not LeadDepartureAssist(0.01).update(**args)


def test_no_positive_floor_when_ego_has_already_exceeded_planned_speed():
  assist, args = LeadDepartureAssist(0.01), inputs()
  assert assist.update(**args)
  args['stopping'] = False
  args['cs'].vEgo = 0.2
  assert not assist.update(**args)


def test_scc_jerk_change_is_scoped_and_bounded():
  assert departure_jerk_upper(0.5, 1.0, 2.0, False) == 0.5
  assert departure_jerk_upper(0.5, 1.0, 2.0, True) == 2.0
  assert departure_jerk_upper(0.5, 1.0, 0.6, True) == 0.6
  assert departure_jerk_upper(0.5, 0.5, 2.0, True) == 1.0
  assert departure_jerk_upper(4.0, 5.0, 6.0, True) == 4.0
