"""Verify actual MPC inputs/costs with a recording solver, not a vehicle model."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from common.conversions import Conversions as CV
from common.numpy_fast import clip, interp
from selfdrive.controls.lib import t_follow
from selfdrive.controls.lib.lead_departure import departure_motion_valid
from selfdrive.controls.lib.lead_following import (NO_LEAD, LeadConfirm, faster_lead_relief,
                                                   get_follow_obstacle_cost)
from selfdrive.modeld.constants import index_function


class RecordingSolver:
  def __init__(self, *args):
    self.values = {}

  def reset(self):
    self.values.clear()

  def set(self, stage, key, value):
    self.values[stage, key] = np.array(value, copy=True)

  cost_set = set


def load_mpc(comfort=True):
  source = Path(__file__).resolve().parents[1] / 'lib' / 'longitudinal_mpc_lib' / 'long_mpc.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  # Leave all real calculations and class methods intact. Only platform
  # imports/build entry points and the native solver execution are isolated.
  tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom, ast.If))]
  env = {name: getattr(t_follow, name) for name in dir(t_follow) if not name.startswith('__')}
  env.update(os=os, np=np, __file__=str(source), CV=CV, clip=clip, interp=interp,
             _CRUISE_GAP_BP=t_follow.CRUISE_GAP_BP, DT_MDL=0.05, index_function=index_function,
             _LEAD_ACCEL_TAU=1.5, AcadosOcpSolverCython=RecordingSolver,
             departure_motion_valid=departure_motion_valid,
             NO_LEAD=NO_LEAD, LeadConfirm=LeadConfirm, faster_lead_relief=faster_lead_relief,
             get_follow_obstacle_cost=get_follow_obstacle_cost if comfort else lambda base, *args: base,
             car=NS(CarState=NS(ButtonEvent=NS(Type=NS(accelCruise=1, resumeCruise=2)))),
             log=NS(LongitudinalPlan=NS(XState=NS(cruise=0, lead=1, softHold=2))))
  exec(compile(tree, str(source), 'exec'), env)
  return env


def scenario(comfort=True, speed=100.0 / 3.6, lead_speed=None, distance=90.0,
             lead_accel=0.0, second=False, traffic_stop=False, mode='acc', braking=False,
             ego_accel=0.2, planned_accel=0.2):
  env = load_mpc(comfort)
  mpc = env['LongitudinalMpc'](mode)
  mpc.run = lambda: None
  mpc.set_cur_state(speed, -0.3 if braking else planned_accel)
  mpc.set_accel_limits(-1.2, 0.8)
  mpc.traffic_stop_active = traffic_stop
  mpc.traffic_stop_distance = 25.0
  lead = NS(status=True, dRel=distance, vLead=speed if lead_speed is None else lead_speed,
            aLeadK=lead_accel, aLeadTau=1.5, modelProb=1.0)
  other = NS(status=second, dRel=20.0, vLead=0.0, aLeadK=0.0, aLeadTau=1.5, modelProb=1.0)
  cs = NS(aEgo=-0.3 if braking else ego_accel, brakePressed=False, gasPressed=False, buttonEvents=[])
  controls = NS(enabled=True, mySafeModeFactor=1.0, longCruiseGap=2)
  refs = [np.zeros(13) for _ in range(4)]
  # These single-frame scenarios assume leads that have been tracked for a
  # while; LeadConfirm would otherwise hold back a far, non-closing new lead.
  mpc.lead0_confirm.update(lead, speed, 1.0)
  mpc.lead1_confirm.update(other, speed, 1.0)
  mpc.update(cs, NS(leadOne=lead, leadTwo=other), controls, 32.0, *refs)
  return mpc


def test_comfort_changes_only_distance_weight_not_obstacles_or_constraints():
  stock, tuned = scenario(False), scenario(True)
  assert stock.solver.values[0, 'W'][0, 0] == 6.0
  assert tuned.solver.values[0, 'W'][0, 0] == pytest.approx(3.9)
  np.testing.assert_array_equal(stock.params, tuned.params)
  for stage in range(12):
    np.testing.assert_array_equal(stock.solver.values[stage, 'Zl'], tuned.solver.values[stage, 'Zl'])
    np.testing.assert_array_equal(stock.solver.values[stage, 'W'][1:, 1:], tuned.solver.values[stage, 'W'][1:, 1:])
  assert stock.source == tuned.source


def test_mpc_follow_cost_is_continuous_at_actual_and_planned_zero_crossings():
  for field in ('ego_accel', 'planned_accel'):
    before = scenario(**{field: 0.001})
    after = scenario(**{field: -0.001})
    assert abs(before.solver.values[0, 'W'][0, 0] - after.solver.values[0, 'W'][0, 0]) < 0.001
    np.testing.assert_array_equal(before.params[:, [0, 1, 2, 4, 5, 6, 7]],
                                  after.params[:, [0, 1, 2, 4, 5, 6, 7]])


@pytest.mark.parametrize('overrides', [dict(speed=5.0), dict(lead_speed=0.0), dict(lead_accel=-1.0),
                                     dict(lead_speed=20.0), dict(distance=20.0), dict(second=True),
                                     dict(traffic_stop=True), dict(mode='blended'), dict(braking=True)])
def test_hazard_stop_and_departure_inputs_match_original_policy(overrides):
  stock, tuned = scenario(False, **overrides), scenario(True, **overrides)
  np.testing.assert_array_equal(stock.params, tuned.params)
  for stage in range(12):
    np.testing.assert_array_equal(stock.solver.values[stage, 'W'], tuned.solver.values[stage, 'W'])
    np.testing.assert_array_equal(stock.solver.values[stage, 'Zl'], tuned.solver.values[stage, 'Zl'])


def test_existing_departure_and_distance_regressions():
  env = load_mpc()
  source = Path(__file__).resolve().parents[1] / 'lib' / 'tests' / 'test_long_mpc_lead_departure.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom))]
  env.update(pytest=pytest, SimpleNamespace=NS)
  exec(compile(tree, str(source), 'exec'), env)
  for name, test in list(env.items()):
    if name.startswith('test_'):
      test()


@pytest.mark.parametrize('boundary', ['relative_speed', 'lead_accel'])
def test_departure_cost_has_no_step_at_motion_gate(boundary):
  env = load_mpc()
  mpc = NS(x0=[0.0, 2.0, 0.0], t_follow=1.15, lead_depart_cost=0.10)
  values = []
  for delta in (-0.00001, 0.00001):
    speed = 2.3 + delta if boundary == 'relative_speed' else 4.0
    accel = -0.2 + delta if boundary == 'lead_accel' else 0.2
    values.append(env['LongitudinalMpc'].get_cost_multipliers(
      mpc, speed, speed, a_lead0=accel, lead0_status=True))
  np.testing.assert_allclose(values[0], values[1], atol=0.0001)


def test_future_speed_crossing_does_not_jump_entire_obstacle():
  equivalence = load_mpc()['get_stopped_equivalence_factor']
  ego = np.array([2.0, 2.0, 2.0])
  before = equivalence(np.array([3.0, 3.0, 2.00001]), ego, krkeegan=True)
  after = equivalence(np.array([3.0, 3.0, 1.99999]), ego, krkeegan=True)
  np.testing.assert_allclose(before, after, atol=0.0001)


def test_departure_offset_never_exceeds_original_bonus_or_extends_speed_range():
  equivalence = load_mpc()['get_stopped_equivalence_factor']
  for speed in (0.0, 2.0, 5.0, 7.0, 30.0 / 3.6, 12.0):
    for relative in (-2.0, 0.0, 0.1, 0.5, 1.0, 4.0):
      lead = max(0.0, speed + relative)
      base = equivalence(lead, speed, krkeegan=False)
      bonus = equivalence(lead, speed, krkeegan=True) - base
      old_bonus = min(max(lead - speed, 0.0), 3.0) * interp(speed, [5.0, 30.0 / 3.6], [1.0, 0.0])
      assert -1e-12 <= bonus <= old_bonus + 1e-12
      if relative >= 1.0:
        assert bonus == pytest.approx(old_bonus)


def run_frames(mpc, lead, speed, frames):
  cs = NS(aEgo=0.0, brakePressed=False, gasPressed=False, buttonEvents=[])
  controls = NS(enabled=True, mySafeModeFactor=1.0, longCruiseGap=2)
  other = NS(status=False, dRel=0.0, vLead=0.0, aLeadK=0.0, aLeadTau=1.5, modelProb=0.0)
  for _ in range(frames):
    mpc.update(cs, NS(leadOne=lead, leadTwo=other), controls, 32.0, *[np.zeros(13) for _ in range(4)])


def fresh_mpc(speed, relief=True):
  env = load_mpc()
  if not relief:
    env['faster_lead_relief'] = lambda *args: 0.0
  mpc = env['LongitudinalMpc']('acc')
  mpc.run = lambda: None
  mpc.set_cur_state(speed, 0.0)
  mpc.set_accel_limits(-1.2, 0.8)
  return mpc


def test_far_new_lead_is_planned_only_after_confirmation():
  speed = 70.0 / 3.6
  lead = NS(status=True, dRel=60.0, vLead=speed, aLeadK=0.0, aLeadTau=1.5, modelProb=1.0)
  mpc = fresh_mpc(speed)
  run_frames(mpc, lead, speed, 1)
  assert not mpc.status
  run_frames(mpc, lead, speed, 5)
  assert mpc.status


def test_close_cut_in_is_planned_at_once():
  speed = 70.0 / 3.6
  lead = NS(status=True, dRel=15.0, vLead=60.0 / 3.6, aLeadK=0.0, aLeadTau=1.5, modelProb=1.0)
  mpc = fresh_mpc(speed)
  run_frames(mpc, lead, speed, 1)
  assert mpc.status and mpc.source == 'lead0'


@pytest.mark.parametrize('v_lead,a_lead,relaxed', [
  (75.0, 0.0, True),     # slightly faster cut-in: gap deficit mostly removed
  (60.0, 0.0, False),    # slower cut-in: normal braking
  (75.0, -1.0, False),   # faster but braking: normal braking
])
def test_faster_cut_in_relief(v_lead, a_lead, relaxed):
  speed = 70.0 / 3.6
  lead = NS(status=True, dRel=15.0, vLead=v_lead / 3.6, aLeadK=a_lead, aLeadTau=1.5, modelProb=1.0)
  with_relief, without = fresh_mpc(speed), fresh_mpc(speed, relief=False)
  run_frames(with_relief, lead, speed, 1)
  run_frames(without, lead, speed, 1)
  gain = with_relief.params[0, 2] - without.params[0, 2]
  if relaxed:
    assert gain > 5.0
  else:
    assert gain == pytest.approx(0.0)


def gap_mpc():
  mpc = load_mpc()['LongitudinalMpc']('acc')
  mpc.tfollow_gaps = [1.1, 1.2, 1.4, 1.6]
  mpc.t_follow_speed_ratio = 1.2
  return mpc


def test_gap_button_applies_while_decelerating():
  mpc = gap_mpc()
  mpc.update_gap_tf(NS(longCruiseGap=2), 80.0 / 3.6)
  before = mpc.t_follow_base
  # Slowing down, user raises the gap: applied at once, not after the decel.
  mpc.update_gap_tf(NS(longCruiseGap=4), 79.0 / 3.6)
  assert mpc.t_follow_base > before + 0.3
  # Slowing down without a gap change: held, as in apilot.
  held = mpc.t_follow_base
  mpc.update_gap_tf(NS(longCruiseGap=4), 70.0 / 3.6)
  assert mpc.t_follow_base == held


def test_held_gap_is_released_gradually_after_deceleration():
  mpc = gap_mpc()
  mpc.update_gap_tf(NS(longCruiseGap=2), 100.0 / 3.6)
  high = mpc.t_follow_base
  for kph in range(99, 29, -1):        # decelerate: base held at the 100 km/h value
    mpc.update_gap_tf(NS(longCruiseGap=2), kph / 3.6)
  assert mpc.t_follow_base == pytest.approx(high)
  values = []
  for _ in range(20):                  # steady at 30 km/h
    mpc.update_gap_tf(NS(longCruiseGap=2), 30.0 / 3.6)
    values.append(mpc.t_follow_base)
  steps = [a - b for a, b in zip([high] + values, values)]
  assert max(steps) <= 0.3 * 0.05 + 1e-9
  assert values[-1] == pytest.approx(1.2 * 1.06)
