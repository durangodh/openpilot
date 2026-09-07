"""Isolate output handling from cereal, CAN and the PID implementation."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS


def brake_output(previous, requested, state='pid'):
  source = Path(__file__).resolve().parents[1] / 'lib' / 'longcontrol.py'
  tree = ast.parse(source.read_text())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LongControl')
  update = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'update')
  states = NS(off='off', pid='pid', stopping='stopping', starting='starting')
  env = dict(LongCtrlState=states, CONTROL_N=2, T_IDXS=[0, 1], DT_CTRL=0.01,
             clip=lambda x, lo, hi: max(lo, min(x, hi)),
             interp=lambda x, bp, values: values[0], apply_deadzone=lambda x, dz: x,
             long_control_state_trans=lambda *args: (state, False))
  exec(compile(ast.Module(body=[update], type_ignores=[]), str(source), 'exec'), env)
  pid = NS(update=lambda *a, **kw: requested, p=requested, i=0.0, d=0.0, f=0.0)
  obj = NS(_read_params=lambda: None, _update_standstill_latch=lambda *a: (False, False),
           CP=NS(stoppingControl=True, longitudinalTuning=NS(deadzoneBP=[0], deadzoneV=[0])),
           actuator_delay_lower=0.2, actuator_delay_upper=0.4, pid=pid,
           long_control_state=state, starting_state=False, last_output_accel=previous,
           starting_ramp_rate=2.0, standstill_hold_memory=None, stop_accel=-0.6,
           stopping_decel_rate=1.0, standstill_hold_accel=-1.1, standstill_hold_rate=1.2,
           reset=lambda *a: None, long_coast_band=0.4)
  cs = NS(vEgo=0.0 if state == 'stopping' else 10.0, brakePressed=False,
          cruiseState=NS(standstill=False))
  plan = NS(speeds=[10.0, 10.0], accels=[0.0, 0.0], jerks=[0.0])
  return env['update'](obj, True, cs, plan, (-3.5, 2.0), 0.0)[0]


def test_pid_braking_passes_without_extra_ramp_or_coast_band():
  for previous in (0.5, 0.0, -0.5):
    for requested in (-0.05, -0.5, -1.2, -3.0):
      assert brake_output(previous, requested) == requested


def test_actuator_limits_still_apply():
  assert brake_output(0.5, -5.0) == -3.5
  assert brake_output(-0.5, 3.0) == 2.0


def test_standstill_hold_is_preserved():
  assert brake_output(-0.6, 0.0, 'stopping') < -0.6
  assert brake_output(-1.1, 0.0, 'stopping') == -1.1
