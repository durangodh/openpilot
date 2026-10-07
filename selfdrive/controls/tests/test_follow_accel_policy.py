"""CruiseHelper exposes only the apilot-c2 speed/mode acceleration table."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from selfdrive.controls.lib import longitudinal_limits as limits


def setup_policy(v_ego=20.0):
  source = Path(__file__).resolve().parents[1] / 'lib' / 'cruise_helper.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CruiseHelper')
  methods = {'get_cruise_max_accel', 'get_longitudinal_accel_limit'}
  cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
  env = {name: getattr(limits, name) for name in dir(limits) if not name.startswith('__')}
  exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), 'exec'), env)
  policy = env['CruiseHelper']()
  policy.cruise_max_vals = [1.0] * 7
  policy.my_driving_mode = 3
  policy.my_eco_mode_factor = 0.8
  policy.my_safe_mode_factor = 1.0
  return policy, NS(vEgo=v_ego)


def test_lead_status_does_not_add_a_second_acceleration_cap():
  policy, cs = setup_policy()
  no_lead = NS(radarState=NS(leadOne=NS(status=False), leadTwo=NS(status=False)))
  with_lead = NS(radarState=NS(leadOne=NS(status=True), leadTwo=NS(status=False)))
  assert policy.get_longitudinal_accel_limit(cs, no_lead, 100.0) == pytest.approx(1.0)
  assert policy.get_longitudinal_accel_limit(cs, with_lead, 100.0) == pytest.approx(1.0)


def test_live_cruise_table_is_the_single_positive_limit():
  policy, cs = setup_policy()
  policy.cruise_max_vals = [0.4] * 7
  assert policy.get_longitudinal_accel_limit(cs, NS(), 100.0) == pytest.approx(0.4)


def test_mode_scaling_stays_in_the_existing_table():
  policy, cs = setup_policy()
  policy.my_driving_mode = 1
  policy.my_safe_mode_factor = 0.7
  assert policy.get_longitudinal_accel_limit(cs, NS(), 100.0) == pytest.approx(
    policy.get_cruise_max_accel(cs.vEgo))
