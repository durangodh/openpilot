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
from selfdrive.controls.lib.lead_following import get_follow_obstacle_cost
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
             get_follow_obstacle_cost=get_follow_obstacle_cost if comfort else lambda base, *args: base,
             car=NS(CarState=NS(ButtonEvent=NS(Type=NS(accelCruise=1, resumeCruise=2)))),
             log=NS(LongitudinalPlan=NS(XState=NS(cruise=0, lead=1, softHold=2))))
  exec(compile(tree, str(source), 'exec'), env)
  return env


def scenario(comfort=True, speed=100.0 / 3.6, lead_speed=None, distance=90.0,
             lead_accel=0.0, second=False, traffic_stop=False, mode='acc', braking=False):
  env = load_mpc(comfort)
  mpc = env['LongitudinalMpc'](mode)
  mpc.run = lambda: None
  mpc.set_cur_state(speed, -0.3 if braking else 0.0)
  mpc.set_accel_limits(-1.2, 0.8)
  mpc.traffic_stop_active = traffic_stop
  mpc.traffic_stop_distance = 25.0
  lead = NS(status=True, dRel=distance, vLead=speed if lead_speed is None else lead_speed,
            aLeadK=lead_accel, aLeadTau=1.5, modelProb=1.0)
  other = NS(status=second, dRel=20.0, vLead=0.0, aLeadK=0.0, aLeadTau=1.5, modelProb=1.0)
  cs = NS(aEgo=-0.3 if braking else 0.0, brakePressed=False, gasPressed=False, buttonEvents=[])
  controls = NS(enabled=True, mySafeModeFactor=1.0, longCruiseGap=2)
  refs = [np.zeros(13) for _ in range(4)]
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
