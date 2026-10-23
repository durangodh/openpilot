"""Exercise Hyundai's actual SCC jerk selection without CAN/cereal bindings."""
import ast
from pathlib import Path
import runpy
from types import SimpleNamespace as NS

import pytest

from common.numpy_fast import clip, interp
from selfdrive.controls.lib.lead_departure import departure_jerk_upper


def scc_limits(assisted=True, state='pid', braking=False, gas=False, soft_hold=False, active=True):
  source = Path(__file__).resolve().parents[2] / 'car' / 'hyundai' / 'carcontroller.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'CarController')
  update = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == 'update_scc')
  # Execute the real prefix, stopping before CAN message construction.
  boundary = next(i for i, node in enumerate(update.body)
                  if isinstance(node, ast.If) and 'self.longcontrol' in ast.unparse(node.test))
  update.body = update.body[:boundary] + [ast.parse('return jerk_upper, jerk_lower, scc_stop_request').body[0]]
  gate = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'should_request_scc_standstill')
  module = ast.fix_missing_locations(ast.Module(body=[gate, update], type_ignores=[]))
  env = dict(clip=clip, interp=interp, DT_CTRL=0.01, departure_jerk_upper=departure_jerk_upper,
             LongCtrlState=NS(off='off', pid='pid', stopping='stopping', starting='starting'))
  exec(compile(module, str(source), 'exec'), env)
  controller = NS(frame=1, soft_hold_mode=2, jerk_start_limit=1.0, jerk_count=0.0,
                  scc_smoother=NS(update=lambda *args: None), packer=None)
  cc = NS(enabled=True, longActive=active)
  cs = NS(out=NS(vEgo=0.0, standstill=True, brakePressed=braking, gasPressed=gas))
  actuators = NS(jerk=0.2, accel=0.1, longControlState=state)
  controls = NS(LoC=NS(long_control_state=state, departure_assist=NS(active=assisted), pid_jerk_accel_mult=1.0))
  return env['update_scc'](controller, cc, cs, actuators, controls, NS(softHold=soft_hold), [])


def test_only_positive_launch_jerk_changes_not_braking_or_stop_request():
  assert scc_limits(False) == (0.5, 1.0, False)
  assert scc_limits(True) == (2.0, 1.0, False)


@pytest.mark.parametrize('kwargs', [dict(braking=True), dict(gas=True), dict(active=False)])
def test_no_scc_launch_boost_under_driver_override_or_inactive(kwargs):
  assert scc_limits(**kwargs) == (0.5, 1.0, False)


def test_stop_and_soft_hold_keep_original_scc_limits():
  assert scc_limits(state='stopping') == (0.5, 5.0, True)
  assert scc_limits(soft_hold=True, braking=True) == (0.5, 5.0, True)
  assert scc_limits(state='off') == (5.0, 5.0, False)


def test_original_scc_stop_request_regressions():
  source = Path(__file__).resolve().parents[2] / 'car' / 'hyundai' / 'tests' / 'test_scc_standstill_request.py'
  # Avoid importing selfdrive.car.__init__ (requires native cereal bindings).
  namespace = runpy.run_path(str(source))
  for name, test in namespace.items():
    if name.startswith('test_'):
      test()
