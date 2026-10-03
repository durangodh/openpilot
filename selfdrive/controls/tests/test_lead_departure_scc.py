"""Exercise Hyundai's actual SCC jerk selection without CAN/cereal bindings."""
import ast
from pathlib import Path
import runpy
from types import SimpleNamespace as NS

import pytest

from common.numpy_fast import clip, interp


def scc_limits(assisted=True, state='pid', braking=False, gas=False, soft_hold=False, active=True,
               pre_release=False):
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
  env = dict(clip=clip, interp=interp, DT_CTRL=0.01, 
             LongCtrlState=NS(off='off', pid='pid', stopping='stopping', starting='starting'))
  exec(compile(module, str(source), 'exec'), env)
  controller = NS(frame=1, soft_hold_mode=2,
                  scc_smoother=NS(update=lambda *args: None), packer=None)
  cc = NS(enabled=True, longActive=active)
  cs = NS(out=NS(vEgo=0.0, standstill=True, brakePressed=braking, gasPressed=gas))
  actuators = NS(jerk=0.2, accel=0.1, longControlState=state)
  controls = NS(LoC=NS(long_control_state=state, departure_assist=NS(active=assisted), pid_jerk_accel_mult=1.0,
                       stopreq_release_active=pre_release))
  return env['update_scc'](controller, cc, cs, actuators, controls, NS(softHold=soft_hold), [])


@pytest.mark.parametrize('kwargs', [dict(assisted=False), dict(assisted=True), dict(state='starting'),
                                    dict(braking=True), dict(gas=True), dict(active=False)])
def test_driving_and_launch_use_fixed_generous_scc_limits(kwargs):
  # Launch smoothing (START JERK LIMIT) now lives in LongControl only.
  assert scc_limits(**kwargs) == (5.0, 5.0, False)


def test_stop_and_soft_hold_keep_original_scc_limits():
  assert scc_limits(state='stopping') == (0.5, 5.0, True)
  assert scc_limits(soft_hold=True, braking=True) == (0.5, 5.0, True)
  assert scc_limits(state='off') == (5.0, 5.0, False)


def test_early_stopreq_release_clears_stop_request_but_keeps_stop_jerk():
  assert scc_limits(state='stopping', pre_release=True) == (0.5, 5.0, False)
  # A stale flag outside stopping never matters.
  assert scc_limits(state='pid', pre_release=True) == (5.0, 5.0, False)


def test_original_scc_stop_request_regressions():
  source = Path(__file__).resolve().parents[2] / 'car' / 'hyundai' / 'tests' / 'test_scc_standstill_request.py'
  # Avoid importing selfdrive.car.__init__ (requires native cereal bindings).
  namespace = runpy.run_path(str(source))
  for name, test in namespace.items():
    if name.startswith('test_'):
      test()
