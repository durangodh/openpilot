"""Exercise the real CruiseHelper policy without vehicle/cereal dependencies."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from common.conversions import Conversions as CV
from selfdrive.controls.lib import longitudinal_limits as limits
from selfdrive.controls.lib.lead_following import (get_closing_lead_accel_limit,
                                                   get_follow_approach_limit,
                                                   get_traffic_accel_limit)


class Messages(dict):
  valid = {'radarState': True, 'longitudinalPlan': True}
  alive = {'radarState': True, 'longitudinalPlan': True}


def setup_policy(v_ego=20.0):
  source = Path(__file__).resolve().parents[1] / 'lib' / 'cruise_helper.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CruiseHelper')
  methods = {'get_lead', 'get_cruise_max_accel', 'get_longitudinal_accel_limit'}
  cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
  env = {name: getattr(limits, name) for name in dir(limits) if not name.startswith('__')}
  env.update(CV=CV, DT_CTRL=0.01,
             get_closing_lead_accel_limit=get_closing_lead_accel_limit,
             get_follow_approach_limit=get_follow_approach_limit,
             get_traffic_accel_limit=get_traffic_accel_limit)
  exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), 'exec'), env)
  policy = env['CruiseHelper']()
  policy.cruise_max_vals = [1.0] * 7
  policy.my_driving_mode = 3
  policy.my_eco_mode_factor = 0.8
  policy.my_safe_mode_factor = 1.0
  policy.no_lead_cruise_accel_factor = 0.65
  policy.current_set_speed_kph = v_ego * 3.6 + 40.0
  cs = NS(vEgo=v_ego, aEgo=0.0)
  lead = NS(status=False, dRel=80.0, vLead=v_ego, aLeadK=0.0)
  sm = Messages({'radarState': NS(leadOne=lead, leadTwo=NS(status=False, dRel=0.0, vLead=0.0, aLeadK=0.0)),
        'longitudinalPlan': NS(longitudinalPlanSource='cruise', desiredDistance=35.0,
                               mpcMode=0, onStop=False, fcw=False)})
  return policy, cs, sm


def limit(policy, cs, sm):
  return policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)


# The allowance only decides how large a positive request may be. Ramping the
# actual request up or down to it is LongControl's PID jerk limit.

def test_lead_acquisition_opens_allowance_directly():
  policy, cs, sm = setup_policy()
  assert limit(policy, cs, sm) == pytest.approx(0.65)
  sm['radarState'].leadOne.status = True
  assert limit(policy, cs, sm) == pytest.approx(1.0)


def test_low_speed_departure_gets_full_allowance():
  policy, cs, sm = setup_policy(v_ego=1.0)
  limit(policy, cs, sm)
  sm['radarState'].leadOne.status = True
  assert limit(policy, cs, sm) == 1.0


def test_live_lower_limit_and_lead_loss_update_the_allowance():
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne.status = True
  assert limit(policy, cs, sm) == 1.0
  policy.cruise_max_vals = [0.4] * 7
  assert limit(policy, cs, sm) == 0.4
  sm['radarState'].leadOne.status = False
  assert limit(policy, cs, sm) == pytest.approx(0.26)


def test_second_lead_gets_the_same_allowance():
  policy, cs, sm = setup_policy()
  limit(policy, cs, sm)
  sm['radarState'].leadTwo.status = True
  assert limit(policy, cs, sm) == pytest.approx(1.0)


def test_plan_source_switch_does_not_change_allowance():
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne.status = True
  for source in ['lead0', 'cruise', 'lead1', 'lead0', 'cruise']:
    sm['longitudinalPlan'].longitudinalPlanSource = source
    assert limit(policy, cs, sm) == 1.0


def test_source_switch_does_not_bypass_real_approach_limit():
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne = NS(status=True, dRel=60.0, vLead=18.0, aLeadK=0.0)
  sm['longitudinalPlan'].desiredDistance = 50.0
  for frame in range(10):
    sm['longitudinalPlan'].longitudinalPlanSource = 'lead0' if frame % 2 else 'cruise'
    assert limit(policy, cs, sm) == pytest.approx(0.25)


def test_far_approach_lifts_throttle_before_follow_gap():
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne = NS(status=True, dRel=60.0, vLead=18.0, aLeadK=0.0)
  sm['longitudinalPlan'].desiredDistance = 50.0
  assert limit(policy, cs, sm) == pytest.approx(0.25)  # 5 seconds until the desired gap


def test_stale_plans_cannot_apply_approach_limit():
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne = NS(status=True, dRel=60.0, vLead=18.0, aLeadK=0.0)
  sm['longitudinalPlan'].desiredDistance = 50.0
  sm.valid = dict(sm.valid, longitudinalPlan=False)
  assert limit(policy, cs, sm) == 1.0


@pytest.mark.parametrize('failure', ['invalid', 'dead', 'radar_error'])
def test_unusable_radar_cannot_open_lead_allowance(failure):
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne.status = True
  if failure == 'invalid':
    sm.valid = dict(sm.valid, radarState=False)
  elif failure == 'dead':
    sm.alive = dict(sm.alive, radarState=False)
  else:
    sm['radarState'].radarErrors = ['canError']
  assert limit(policy, cs, sm) == pytest.approx(0.65)


@pytest.mark.parametrize('failure', ['invalid', 'dead', 'radar_error'])
def test_unusable_radar_cannot_feed_stale_lead_to_resume_logic(failure):
  policy, _, sm = setup_policy()
  sm['radarState'].leadOne.status = True
  if failure == 'invalid':
    sm.valid = dict(sm.valid, radarState=False)
  elif failure == 'dead':
    sm.alive = dict(sm.alive, radarState=False)
  else:
    sm['radarState'].radarErrors = ['canError']
  assert policy.get_lead(sm) is None


@pytest.mark.parametrize('guard', ['stop', 'fcw', 'blended', 'stopped_lead', 'braking_lead'])
def test_approach_policy_is_not_applied_to_stop_or_hazard_scenes(guard):
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne = NS(status=True, dRel=60.0, vLead=18.0, aLeadK=0.0)
  plan = sm['longitudinalPlan']
  plan.desiredDistance = 50.0
  if guard == 'stop':
    plan.onStop = True
  elif guard == 'fcw':
    plan.fcw = True
  elif guard == 'blended':
    plan.mpcMode = 1
  elif guard == 'stopped_lead':
    sm['radarState'].leadOne.vLead = 0.0
  elif guard == 'braking_lead':
    sm['radarState'].leadOne.aLeadK = -1.0
  expected = 1.0
  if plan.mpcMode == 0:
    expected = get_traffic_accel_limit(
      expected, cs.vEgo, sm['radarState'].leadOne, plan.desiredDistance)
    expected = get_closing_lead_accel_limit(
      expected, cs.vEgo, (sm['radarState'].leadOne, sm['radarState'].leadTwo),
      plan.desiredDistance, cs.aEgo)
  assert limit(policy, cs, sm) == pytest.approx(expected)
