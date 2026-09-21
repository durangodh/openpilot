"""Exercise the real CruiseHelper policy without vehicle/cereal dependencies."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from common.conversions import Conversions as CV
from selfdrive.controls.lib import longitudinal_limits as limits
from selfdrive.controls.lib.lead_following import APPROACH_ACCEL_LIMIT_FALL, get_follow_approach_limit


class Messages(dict):
  valid = {'radarState': True, 'longitudinalPlan': True}
  alive = {'radarState': True, 'longitudinalPlan': True}


def setup_policy(v_ego=20.0):
  source = Path(__file__).resolve().parents[1] / 'lib' / 'cruise_helper.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CruiseHelper')
  methods = {'get_cruise_max_accel', 'get_longitudinal_accel_limit', 'get_apply_accel'}
  cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
  env = {name: getattr(limits, name) for name in dir(limits) if not name.startswith('__')}
  env.update(CV=CV, DT_CTRL=0.01, get_follow_approach_limit=get_follow_approach_limit,
             APPROACH_ACCEL_LIMIT_FALL=APPROACH_ACCEL_LIMIT_FALL)
  exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), 'exec'), env)
  policy = env['CruiseHelper']()
  policy.cruise_max_vals = [1.0] * 7
  policy.my_driving_mode = 3
  policy.my_eco_mode_factor = 0.8
  policy.my_safe_mode_factor = 1.0
  policy.no_lead_cruise_accel_factor = 0.65
  policy.no_lead_cruise_jerk_limit = 0.25
  policy.follow_accel_limit = None
  policy.follow_source = None
  policy.last_apply_accel = 0.65
  policy.current_set_speed_kph = v_ego * 3.6 + 40.0
  cs = NS(vEgo=v_ego)
  lead = NS(status=False, dRel=80.0, vLead=v_ego, aLeadK=0.0)
  sm = Messages({'radarState': NS(leadOne=lead, leadTwo=NS(status=False)),
        'longitudinalPlan': NS(longitudinalPlanSource='cruise', desiredDistance=35.0,
                               mpcMode=0, onStop=False, fcw=False)})
  return policy, cs, sm


def test_lead_acquisition_does_not_jump_pid_or_scc_acceleration():
  policy, cs, sm = setup_policy()
  before = policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)
  assert before == pytest.approx(0.65)
  sm['radarState'].leadOne.status = True
  previous = before
  for _ in range(110):
    cap = policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)
    output = policy.get_apply_accel(NS(out=cs), sm, 2.0, False, dt=0.02)
    assert output <= cap <= 1.0
    assert cap - previous <= limits.FOLLOW_ACCEL_LIMIT_RISE * 0.01 + 1e-9
    previous = cap
  assert cap == pytest.approx(1.0)


@pytest.mark.parametrize('accel,stopping', [(-3.5, False), (-1.1, True), (0.0, False)])
def test_acquisition_never_holds_back_braking(accel, stopping):
  policy, cs, sm = setup_policy()
  policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)
  sm['radarState'].leadOne.status = True
  policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)
  assert policy.get_apply_accel(NS(out=cs), sm, accel, stopping) == accel


def test_low_speed_departure_does_not_wait_for_cap_ramp():
  policy, cs, sm = setup_policy(v_ego=1.0)
  policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)
  sm['radarState'].leadOne.status = True
  assert policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph) == 1.0
  assert policy.get_apply_accel(NS(out=cs), sm, 0.35, False) == 0.35


def test_live_lower_limit_and_lead_loss_take_effect_without_delay():
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne.status = True
  assert policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph) == 1.0
  policy.cruise_max_vals = [0.4] * 7
  # The final transport cap is valid even before the next controller frame.
  assert policy.get_apply_accel(NS(out=cs), sm, 1.5, False) == 0.4
  assert policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph) == 0.4
  sm['radarState'].leadOne.status = False
  assert policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph) == pytest.approx(0.26)


def test_second_lead_gets_the_same_pid_and_transport_cap():
  policy, cs, sm = setup_policy()
  policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)
  sm['radarState'].leadTwo.status = True
  cap = policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)
  assert cap == pytest.approx(0.6535)
  assert policy.get_apply_accel(NS(out=cs), sm, 2.0, False) == cap


def test_distant_source_switch_reopens_from_actual_output():
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne.status = True
  assert policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph) == 1.0
  policy.last_apply_accel = 0.2
  sm['longitudinalPlan'].longitudinalPlanSource = 'lead0'
  cap = policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)
  assert cap == pytest.approx(0.2035)
  assert policy.get_apply_accel(NS(out=cs), sm, 1.0, False) == cap
  assert policy.get_apply_accel(NS(out=cs), sm, -3.5, False) == -3.5


def test_far_approach_lifts_throttle_before_follow_gap():
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne = NS(status=True, dRel=60.0, vLead=18.0, aLeadK=0.0)
  sm['longitudinalPlan'].desiredDistance = 50.0
  cap = policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)
  assert cap == pytest.approx(0.25)  # 5 seconds until the desired gap
  assert policy.get_apply_accel(NS(out=cs), sm, 1.0, False) == cap
  assert policy.get_apply_accel(NS(out=cs), sm, -2.0, False) == -2.0


def test_throttle_lift_is_gradual_but_braking_is_immediate():
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne.status = True
  previous = policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)
  sm['radarState'].leadOne = NS(status=True, dRel=60.0, vLead=18.0, aLeadK=0.0)
  sm['longitudinalPlan'].desiredDistance = 50.0
  for _ in range(150):
    cap = policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph)
    assert 0.0 <= previous - cap <= APPROACH_ACCEL_LIMIT_FALL * 0.01 + 1e-9
    assert policy.get_apply_accel(NS(out=cs), sm, -3.5, False) == -3.5
    previous = cap
  assert cap == pytest.approx(0.25)


def test_stale_plans_cannot_apply_approach_or_source_comfort():
  policy, cs, sm = setup_policy()
  sm['radarState'].leadOne = NS(status=True, dRel=60.0, vLead=18.0, aLeadK=0.0)
  sm['longitudinalPlan'].desiredDistance = 50.0
  sm.valid = dict(sm.valid, longitudinalPlan=False)
  assert policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph) == 1.0
  assert policy.follow_source is None


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
  assert policy.get_longitudinal_accel_limit(cs, sm, policy.current_set_speed_kph) == 1.0
  assert policy.get_apply_accel(NS(out=cs), sm, -3.5, False) == -3.5
