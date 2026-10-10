"""CruiseHelper: apilot-c2 speed/mode table, x NO-LEAD CRUISE ACCEL only when there is no lead."""
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
  policy.no_lead_cruise_accel_factor = 1.0
  return policy, NS(vEgo=v_ego)


NO_LEAD = {'radarState': NS(leadOne=NS(status=False), leadTwo=NS(status=False))}
WITH_LEAD = {'radarState': NS(leadOne=NS(status=True), leadTwo=NS(status=False))}


def test_no_lead_factor_100_keeps_the_table():
  policy, cs = setup_policy()
  assert policy.get_longitudinal_accel_limit(cs, NO_LEAD, 100.0) == pytest.approx(1.0)
  assert policy.get_longitudinal_accel_limit(cs, WITH_LEAD, 100.0) == pytest.approx(1.0)


def test_no_lead_factor_scales_only_without_a_lead():
  policy, cs = setup_policy()
  policy.no_lead_cruise_accel_factor = 0.95
  assert policy.get_longitudinal_accel_limit(cs, NO_LEAD, 100.0) == pytest.approx(0.95)
  assert policy.get_longitudinal_accel_limit(cs, WITH_LEAD, 100.0) == pytest.approx(1.0)
  policy.no_lead_cruise_accel_factor = 1.05
  assert policy.get_longitudinal_accel_limit(cs, NO_LEAD, 100.0) == pytest.approx(1.05)


def test_live_cruise_table_is_the_single_positive_limit():
  policy, cs = setup_policy()
  policy.cruise_max_vals = [0.4] * 7
  assert policy.get_longitudinal_accel_limit(cs, WITH_LEAD, 100.0) == pytest.approx(0.4)


def test_mode_scaling_stays_in_the_existing_table():
  policy, cs = setup_policy()
  policy.my_driving_mode = 1
  policy.my_safe_mode_factor = 0.7
  assert policy.get_longitudinal_accel_limit(cs, WITH_LEAD, 100.0) == pytest.approx(
    policy.get_cruise_max_accel(cs.vEgo))
