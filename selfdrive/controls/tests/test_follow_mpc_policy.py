"""Verify the simplified apilot-c2 MPC policy with a recording solver."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from common.conversions import Conversions as CV
from common.numpy_fast import clip, interp
from selfdrive.controls.lib import t_follow
from selfdrive.controls.lib.lead_following import NO_LEAD, LeadConfirm
from selfdrive.modeld.constants import index_function


class RecordingSolver:
  def __init__(self, *args):
    self.values = {}

  def reset(self):
    self.values.clear()

  def set(self, stage, key, value):
    self.values[stage, key] = np.array(value, copy=True)

  cost_set = set


def load_mpc():
  source = Path(__file__).resolve().parents[1] / 'lib' / 'longitudinal_mpc_lib' / 'long_mpc.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  tree.body = [n for n in tree.body if not isinstance(n, (ast.Import, ast.ImportFrom, ast.If))]
  env = {name: getattr(t_follow, name) for name in dir(t_follow) if not name.startswith('__')}
  env.update(os=os, np=np, __file__=str(source), CV=CV, clip=clip, interp=interp,
             _CRUISE_GAP_BP=t_follow.CRUISE_GAP_BP, DT_MDL=0.05,
             index_function=index_function, _LEAD_ACCEL_TAU=1.5,
             AcadosOcpSolverCython=RecordingSolver, NO_LEAD=NO_LEAD,
             LeadConfirm=LeadConfirm, sec_since_boot=lambda: 0.0,
             cloudlog=NS(error=lambda *args, **kwargs: None),
             car=NS(CarState=NS(ButtonEvent=NS(Type=NS(accelCruise=1, resumeCruise=2)))),
             log=NS(LongitudinalPlan=NS(XState=NS(cruise=0, lead=1, softHold=2))))
  exec(compile(tree, str(source), 'exec'), env)
  return env


def scenario(speed=5.0, lead_speed=7.0, distance=30.0, dynamic=True):
  env = load_mpc()
  mpc = env['LongitudinalMpc']('acc')
  mpc.run = lambda: None
  mpc.applyLongDynamicCost = dynamic
  mpc.set_cur_state(speed, 0.0)
  mpc.set_accel_limits(-1.2, 1.0)
  lead = NS(status=True, dRel=distance, vLead=lead_speed, aLeadK=0.0,
            aLeadTau=1.5, modelProb=1.0, radar=True)
  other = NS(status=False, dRel=0.0, vLead=0.0, aLeadK=0.0,
             aLeadTau=1.5, modelProb=0.0, radar=False)
  mpc.lead0_confirm.update(lead, speed, 1.0)
  cs = NS(aEgo=0.0, brakePressed=False, gasPressed=False, buttonEvents=[])
  controls = NS(enabled=True, mySafeModeFactor=1.0, longCruiseGap=2)
  mpc.update(cs, NS(leadOne=lead, leadTwo=other), controls, 32.0,
             *[np.zeros(13) for _ in range(4)])
  return mpc


def test_mpc_keeps_one_configured_obstacle_cost():
  mpc = scenario()
  assert mpc.solver.values[0, 'W'][0, 0] == pytest.approx(6.0)


def test_original_apilot_dynamic_cost_reduces_accel_and_jerk_cost_together():
  normal = scenario(dynamic=False)
  dynamic = scenario(dynamic=True)
  assert dynamic.solver.values[0, 'W'][4, 4] < normal.solver.values[0, 'W'][4, 4]
  assert dynamic.solver.values[0, 'W'][5, 5] < normal.solver.values[0, 'W'][5, 5]


def run_frames(mpc, lead, speed, frames):
  cs = NS(aEgo=0.0, brakePressed=False, gasPressed=False, buttonEvents=[])
  controls = NS(enabled=True, mySafeModeFactor=1.0, longCruiseGap=2)
  other = NS(status=False, dRel=0.0, vLead=0.0, aLeadK=0.0,
             aLeadTau=1.5, modelProb=0.0, radar=False)
  for _ in range(frames):
    mpc.update(cs, NS(leadOne=lead, leadTwo=other), controls, 32.0,
               *[np.zeros(13) for _ in range(4)])


def fresh_mpc(speed):
  mpc = load_mpc()['LongitudinalMpc']('acc')
  mpc.run = lambda: None
  mpc.set_cur_state(speed, 0.0)
  mpc.set_accel_limits(-1.2, 0.8)
  return mpc


def test_far_new_lead_is_planned_only_after_confirmation():
  speed = 70.0 / 3.6
  lead = NS(status=True, dRel=60.0, vLead=speed, aLeadK=0.0,
            aLeadTau=1.5, modelProb=1.0, radar=True)
  mpc = fresh_mpc(speed)
  run_frames(mpc, lead, speed, 1)
  assert not mpc.status
  run_frames(mpc, lead, speed, 5)
  assert mpc.status


def test_close_cut_in_is_planned_at_once():
  speed = 70.0 / 3.6
  lead = NS(status=True, dRel=15.0, vLead=60.0 / 3.6, aLeadK=0.0,
            aLeadTau=1.5, modelProb=1.0, radar=True)
  mpc = fresh_mpc(speed)
  run_frames(mpc, lead, speed, 1)
  assert mpc.status and mpc.source == 'lead0'


def gap_mpc():
  mpc = load_mpc()['LongitudinalMpc']('acc')
  mpc.tfollow_gaps = [1.1, 1.2, 1.4, 1.6]
  mpc.t_follow_speed_ratio = 1.2
  return mpc


def test_gap_button_applies_while_decelerating():
  mpc = gap_mpc()
  mpc.update_gap_tf(NS(longCruiseGap=2), 80.0 / 3.6)
  before = mpc.t_follow_base
  mpc.update_gap_tf(NS(longCruiseGap=4), 79.0 / 3.6)
  assert mpc.t_follow_base > before + 0.3
  held = mpc.t_follow_base
  mpc.update_gap_tf(NS(longCruiseGap=4), 70.0 / 3.6)
  assert mpc.t_follow_base == held


def test_held_gap_releases_gradually_after_deceleration():
  mpc = gap_mpc()
  mpc.update_gap_tf(NS(longCruiseGap=2), 100.0 / 3.6)
  high = mpc.t_follow_base
  for kph in range(99, 29, -1):
    mpc.update_gap_tf(NS(longCruiseGap=2), kph / 3.6)
  assert mpc.t_follow_base == pytest.approx(high)
  values = []
  for _ in range(20):
    mpc.update_gap_tf(NS(longCruiseGap=2), 30.0 / 3.6)
    values.append(mpc.t_follow_base)
  steps = [a - b for a, b in zip([high] + values, values)]
  assert max(steps) <= 0.3 * 0.05 + 1e-9
