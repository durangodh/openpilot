import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from common.numpy_fast import interp
from selfdrive.controls.lib.t_follow import clamp_desired_follow_distance


SOURCE = Path(__file__).parents[1] / 'longitudinal_mpc_lib' / 'long_mpc.py'
tree = ast.parse(SOURCE.read_text())
wanted = {'get_stopped_equivalence_factor', 'get_safe_obstacle_distance',
          'desired_follow_distance'}
functions = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in wanted]
mpc_class = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == 'LongitudinalMpc')
cost_method = next(node for node in mpc_class.body
                   if isinstance(node, ast.FunctionDef) and node.name == 'get_cost_multipliers')
test_class = ast.ClassDef(name='LongitudinalMpc', bases=[], keywords=[],
                          body=[cost_method], decorator_list=[])
env = dict(np=np, interp=interp, clamp_desired_follow_distance=clamp_desired_follow_distance,
           T_FOLLOW=1.45, COMFORT_BRAKE=2.5, STOP_DISTANCE=6.0)
module = ast.fix_missing_locations(ast.Module(body=functions + [test_class], type_ignores=[]))
exec(compile(module,
             str(SOURCE), 'exec'), env)

LongitudinalMpc = env['LongitudinalMpc']
get_stopped_equivalence_factor = env['get_stopped_equivalence_factor']
get_safe_obstacle_distance = env['get_safe_obstacle_distance']
desired_follow_distance = env['desired_follow_distance']


def cost_multipliers(v_ego, lead0, lead1, t_follow=1.45):
  mpc = SimpleNamespace(x0=[0.0, v_ego, 0.0], t_follow=t_follow)
  return LongitudinalMpc.get_cost_multipliers(mpc, lead0, lead1)


def test_original_apilot_cost_reacts_directly_to_leads_moving_away():
  assert cost_multipliers(0.0, 1.0, 1.0) == pytest.approx((0.05, 0.05, 1.0))
  assert cost_multipliers(2.0, 4.0, 4.0) == pytest.approx((0.24, 0.24, 1.0))
  assert cost_multipliers(5.0, 7.0, 7.0) == pytest.approx((0.525, 0.525, 1.0))
  assert cost_multipliers(10.0, 12.0, 12.0) == pytest.approx((1.0, 1.0, 1.0))


def test_cost_reduction_is_not_applied_while_either_lead_is_slower():
  assert cost_multipliers(2.0, 1.9, 4.0) == pytest.approx((1.0, 1.0, 1.0))
  assert cost_multipliers(2.0, 4.0, 1.9) == pytest.approx((1.0, 1.0, 1.0))


def test_time_gap_cost_matches_apilot_without_speed_window_gate():
  assert cost_multipliers(5.0, 4.0, 4.0, 1.2) == pytest.approx((0.8, 0.8, 1.3))
  assert cost_multipliers(5.0, 4.0, 4.0, 1.8) == pytest.approx((1.0, 1.0, 1.0))


def test_departing_lead_distance_bonus_uses_original_apilot_fade():
  v_ego, v_lead = 5.0, 7.0
  base = get_stopped_equivalence_factor(v_lead, v_ego, krkeegan=False)
  dynamic = get_stopped_equivalence_factor(v_lead, v_ego, krkeegan=True)
  assert dynamic - base == pytest.approx(1.0)

  v_ego = 30.0 / 3.6
  base = get_stopped_equivalence_factor(12.0, v_ego, krkeegan=False)
  dynamic = get_stopped_equivalence_factor(12.0, v_ego, krkeegan=True)
  assert dynamic - base == pytest.approx(0.5)


def test_desired_follow_distance_matches_mpc_obstacle_model():
  v_ego, v_lead, t_follow, stop_dist = 5.0, 7.0, 1.3, 6.0
  expected = max(0.0,
                 get_safe_obstacle_distance(v_ego, t_follow, stop_dist, 2.5) -
                 get_stopped_equivalence_factor(v_lead, v_ego, t_follow, stop_dist,
                                                krkeegan=True, comfort_brake=2.5))
  assert desired_follow_distance(v_ego, v_lead, t_follow, stop_dist,
                                 2.5, krkeegan=True) == pytest.approx(expected)
