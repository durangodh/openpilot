#!/usr/bin/env python3
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np


def _load_planner_bridge():
  source = Path(__file__).resolve().parents[1] / 'lib' / 'longitudinal_planner.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'get_accel_from_plan')
  env = {
    'CONTROL_N': 17,
    'DT_MDL': 0.05,
    'T_IDXS': np.array([0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4,
                        0.5, 0.6, 0.8, 1.0, 1.3, 1.6, 2.0, 2.5]),
    'interp': np.interp,
  }
  exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), env)
  return env['get_accel_from_plan']


get_accel_from_plan = _load_planner_bridge()


def _load_longcontrol_guards():
  source = Path(__file__).resolve().parents[1] / 'lib' / 'longcontrol.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LongControl')
  names = {
    '_trim_positive_overshoot_integrator',
    '_cap_positive_output_on_negative_target',
    '_apply_moving_stop_target_follow',
    '_relax_stop_overbrake',
  }
  methods = []
  for node in cls.body:
    if isinstance(node, ast.FunctionDef) and node.name in names:
      node.decorator_list = []
      methods.append(node)
  env = {
    'interp': np.interp,
    'DT_CTRL': 0.01,
    'MOVING_STOP_FOLLOW_MIN_GAP': 0.25,
    'NEGATIVE_TARGET_CREEP_GUARD_SPEED': 0.35,
    'NEGATIVE_TARGET_CREEP_GUARD_DECEL': 0.40,
  }
  exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), str(source), 'exec'), env)
  return env


guards = _load_longcontrol_guards()


def test_decelerating_mpc_plan_produces_negative_accel_target():
  speeds = np.linspace(15.0, 0.0, 17)
  accels = np.full(17, -1.0)
  a_target, should_stop = get_accel_from_plan(speeds, accels)

  assert a_target < 0.0
  assert not should_stop


def test_stop_requires_low_speed_now_and_one_second_ahead():
  speeds = np.zeros(17)
  accels = np.zeros(17)
  _, should_stop = get_accel_from_plan(speeds, accels, v_ego_stopping=0.1)
  assert should_stop

  speeds[12:] = 1.0
  _, should_stop = get_accel_from_plan(speeds, accels, v_ego_stopping=0.1)
  assert not should_stop


def test_invalid_plan_is_fail_safe_stop():
  a_target, should_stop = get_accel_from_plan([], [])
  assert a_target == 0.0
  assert should_stop


def test_negative_mpc_target_removes_stale_positive_drive():
  cs = NS(vEgo=10.0, aEgo=0.4, brakePressed=False)
  controller = NS(pid=NS(i=0.4))
  guards['_trim_positive_overshoot_integrator'](controller, -0.8, -1.2, cs)
  assert controller.pid.i < 0.1

  output = guards['_cap_positive_output_on_negative_target'](0.5, -0.8, -1.2, cs)
  assert output <= 0.0


def test_moving_stop_follows_mpc_gradually():
  cs = NS(vEgo=8.0, brakePressed=False)
  output = guards['_apply_moving_stop_target_follow'](0.0, -1.0, True, cs, 0.1)
  assert -1.0 < output < 0.0

  # The helper must not inject this extra ramp after the driver presses brake.
  cs.brakePressed = True
  assert guards['_apply_moving_stop_target_follow'](0.0, -1.0, True, cs, 0.1) == 0.0


def test_stopping_relaxes_overbrake_toward_stop_accel_while_rolling():
  # 2026-10-19: STOPPING ACCEL 15 (-0.30) but stopping held -0.52 below 2 km/h.
  relax = guards['_relax_stop_overbrake']
  cs = NS(vEgo=0.5, standstill=False)
  out = -0.52
  for _ in range(10):
    out = relax(out, -0.30, 1.2, cs, 0.3)
  assert abs(out - (-0.40)) < 1e-6
  for _ in range(100):
    out = relax(out, -0.30, 1.2, cs, 0.3)
  assert out == -0.30


def test_stopping_relax_leaves_weaker_standstill_and_fast_cases():
  relax = guards['_relax_stop_overbrake']
  assert relax(-0.20, -0.30, 1.2, NS(vEgo=0.5, standstill=False), 0.3) == -0.20
  assert relax(-0.52, -0.30, 1.2, NS(vEgo=0.0, standstill=True), 0.3) == -0.52
  assert relax(-0.90, -0.30, 1.2, NS(vEgo=3.0, standstill=False), 0.3) == -0.90
