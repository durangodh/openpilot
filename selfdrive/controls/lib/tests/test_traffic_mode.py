"""Traffic profile and real planner/MPC wiring without the EON native solver.

The recording solver verifies inputs/costs; it does not simulate vehicle motion.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from selfdrive.controls.lib.traffic_mode import TrafficMode
from selfdrive.modeld.constants import T_IDXS
from selfdrive.controls.tests.test_follow_mpc_policy import load_mpc


class Messages(dict):
  valid = True

  def all_checks(self, service_list=None):
    return self.valid


def messages(speed=5.0, distance=30.0, relative=0.0, lead_accel=0.0, ego_accel=0.0):
  lead = NS(status=True, dRel=distance, vRel=relative, vLead=max(0, speed + relative),
            aLeadK=lead_accel, aLeadTau=1.5, modelProb=1.0)
  return Messages(carState=NS(vEgo=speed, aEgo=ego_accel, standstill=speed == 0,
                              gasPressed=False, brakePressed=False, buttonEvents=[]),
                  radarState=NS(leadOne=lead, leadTwo=NS(status=False)),
                  modelV2=NS(position=NS(x=np.linspace(0, 100, 33))),
                  controlsState=NS(enabled=True, mySafeModeFactor=1.0, longCruiseGap=2))


def update(traffic, sm, **kwargs):
  args = dict(available=True, model_distance=100.0, model_time=10.0,
              max_accel=1.6, min_accel=-4.0, stop_distance=6.0,
              comfort_brake=2.5, danger_factor=0.8, initial_t_follow=1.2)
  args.update(kwargs)
  traffic.update(sm, **args)


@pytest.mark.parametrize('speed,follow', [(0, .5), (2, .583), (5, .638),
                                         (10, .939), (15, 1.197), (20, 1.15), (25, 1.15)])
def test_original_speed_profile_and_activation_rate_limit(speed, follow):
  traffic, sm = TrafficMode(.05), messages(speed=speed, distance=60)
  update(traffic, sm)
  assert traffic.get_base_t_follow(sm) == pytest.approx(follow)
  assert abs(traffic.t_follow - 1.2) <= .06 + 1e-9
  for _ in range(400):
    update(traffic, sm)
  assert traffic.t_follow == pytest.approx(follow)


def test_pullaway_and_braking_response():
  launch = TrafficMode(.05)
  update(launch, messages(speed=0, relative=1.5, distance=10))
  assert launch.acceleration_jerk == pytest.approx(.75)
  assert launch.speed_jerk == pytest.approx(.75)
  assert launch.max_accel == pytest.approx(1.6)
  assert launch.danger_factor == pytest.approx(.7)
  braking = TrafficMode(.05)
  update(braking, messages(speed=20, relative=-20, distance=20,
                           lead_accel=-3, ego_accel=-1))
  assert braking.t_follow > 1.2
  assert braking.min_accel == pytest.approx(-4)
  assert braking.danger_factor == pytest.approx(.9)
  assert braking.acceleration_jerk == pytest.approx(2)
  assert braking.danger_jerk == pytest.approx(2)


@pytest.mark.parametrize('cap', [0.0, .2, .8, 1.3])
def test_pullaway_never_raises_user_or_cornering_accel_limit(cap):
  traffic = TrafficMode(.05)
  update(traffic, messages(speed=1, relative=4), max_accel=cap)
  assert traffic.active and traffic.max_accel <= cap


@pytest.mark.parametrize('kwargs', [dict(available=False), dict(model_distance=-1),
                                     dict(model_distance=float('nan'))])
def test_unavailable_input_clears_filter(kwargs):
  traffic, sm = TrafficMode(.05), messages()
  update(traffic, sm)
  update(traffic, sm, **kwargs)
  assert not traffic.active and traffic.filtered_t_follow is None


@pytest.mark.parametrize('field,value', [('status', False), ('dRel', 0),
                                         ('vRel', float('nan')), ('aLeadK', float('inf'))])
def test_lead_loss_and_invalid_lead_reset(field, value):
  traffic, sm = TrafficMode(.05), messages()
  update(traffic, sm)
  setattr(sm['radarState'].leadOne, field, value)
  update(traffic, sm)
  assert not traffic.active


def test_closing_lead_enters_horizon_and_stop_retains_tracking():
  traffic = TrafficMode(.05)
  update(traffic, messages(speed=20, distance=130))
  assert not traffic.active
  update(traffic, messages(speed=20, distance=130, relative=-5))
  assert traffic.active
  update(traffic, messages(speed=0, distance=7), model_distance=0)
  assert traffic.active
  lost = messages(speed=0, distance=7)
  lost['radarState'].leadOne.status = False
  update(traffic, lost, model_distance=0)
  assert not traffic.active


def setup_planner():
  env = load_mpc()
  mpc = env['LongitudinalMpc']()
  source = Path(__file__).resolve().parents[1] / 'longitudinal_planner.py'
  cls = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.ClassDef))
  method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'update_traffic_mode')
  env = dict(env, T_IDXS=T_IDXS)
  exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), env)
  planner = NS(mpc=mpc, CP=NS(openpilotLongitudinalControl=True), traffic_mode_enabled=True)
  return planner, env['update_traffic_mode']


@pytest.mark.parametrize('blocker', ['disabled', 'stock_acc', 'disengaged', 'reset', 'e2e',
                                    'traffic_stop', 'hold', 'gas', 'brake', 'invalid', 'model'])
def test_planner_gate_restores_unmodified_limits(blocker):
  planner, step = setup_planner()
  sm = messages()
  step(planner, sm, False, [-1.2, 1.6])
  assert planner.mpc.traffic.active
  if blocker == 'disabled': planner.traffic_mode_enabled = False
  if blocker == 'stock_acc': planner.CP.openpilotLongitudinalControl = False
  if blocker == 'disengaged': sm['controlsState'].enabled = False
  if blocker == 'e2e': planner.mpc.mode = 'blended'
  if blocker == 'traffic_stop': planner.mpc.traffic_stop_active = True
  if blocker == 'hold': planner.mpc.xState = 2
  if blocker == 'gas': sm['carState'].gasPressed = True
  if blocker == 'brake': sm['carState'].brakePressed = True
  if blocker == 'invalid': sm.valid = False
  if blocker == 'model': sm['modelV2'].position.x = []
  limits = [-1.2, 1.6]
  step(planner, sm, blocker == 'reset', limits)
  assert not planner.mpc.traffic.active
  assert limits == [-1.2, 1.6]


def run_mpc(mpc, sm):
  mpc.set_cur_state(sm['carState'].vEgo, sm['carState'].aEgo)
  mpc.set_accel_limits(-1.5, 1.2)
  mpc.run = lambda: None
  mpc.update(sm['carState'], sm['radarState'], sm['controlsState'], 20,
             *[np.zeros(13) for _ in range(4)])


def test_mpc_uses_traffic_costs_and_obstacles_without_c2_departure_stacking():
  env = load_mpc()
  mpc, sm = env['LongitudinalMpc'](), messages(speed=1, relative=2)
  mpc.applyLongDynamicCost = True
  update(mpc.traffic, sm)
  run_mpc(mpc, sm)
  assert mpc.solver.values[0, 'W'][4, 4] == pytest.approx(200 * mpc.traffic.acceleration_jerk)
  assert mpc.solver.values[0, 'W'][5, 5] == pytest.approx(5 * mpc.traffic.speed_jerk)
  assert mpc.solver.values[0, 'Zl'][3] == pytest.approx(100 * mpc.traffic.danger_jerk)
  assert np.all(mpc.params[:, 0] == -4)  # full braking authority remains available
  assert np.all(mpc.params[:, 4] == mpc.traffic.t_follow)
  assert np.all(mpc.params[:, 5] == mpc.traffic.danger_factor)
  assert mpc.desired_distance == pytest.approx(
    env['desired_follow_distance'](1, 3, mpc.t_follow, mpc.stop_dist, mpc.comfort_brake))
  # Turning the feature off must restore precisely the existing C2 behavior.
  mpc.traffic.reset()
  run_mpc(mpc, sm)
  reference = env['LongitudinalMpc']()
  reference.applyLongDynamicCost = True
  run_mpc(reference, sm)
  assert mpc.params == pytest.approx(reference.params)
  assert mpc.solver.values[0, 'W'] == pytest.approx(reference.solver.values[0, 'W'])


def test_second_stopped_lead_remains_an_mpc_obstacle():
  env = load_mpc()
  mpc, sm = env['LongitudinalMpc'](), messages(speed=5, distance=30, relative=2)
  sm['radarState'].leadTwo = NS(status=True, dRel=12, vLead=0, aLeadK=0, aLeadTau=1.5)
  update(mpc.traffic, sm)
  run_mpc(mpc, sm)
  assert mpc.source == 'lead1'
  assert mpc.params[0, 2] == pytest.approx(12)


def test_safe_mode_distance_model_matches_mpc():
  planner, step = setup_planner()
  sm = messages(speed=20, relative=-20, distance=80)
  sm['controlsState'].mySafeModeFactor = .8
  step(planner, sm, False, [-1.2, .7])
  run_mpc(planner.mpc, sm)
  assert planner.mpc.desired_distance == pytest.approx(
    planner.mpc.traffic.desired_follow_distance(20, 0, planner.mpc.t_follow))
  assert planner.mpc.traffic.max_accel <= .7
