"""Isolate output handling from cereal, CAN and the PID implementation."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS


def brake_output(previous, requested, state='pid', v_ego=None, brake_pressed=False,
                 hold_active=False):
  source = Path(__file__).resolve().parents[1] / 'lib' / 'longcontrol.py'
  tree = ast.parse(source.read_text())
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LongControl')
  update = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'update')
  states = NS(off='off', pid='pid', stopping='stopping', starting='starting')
  env = dict(LongCtrlState=states, CONTROL_N=2, T_IDXS=[0, 1], DT_CTRL=0.01,
             LEAD_DROPOUT_FALLBACK_FRAMES=150,
             PID_JERK_SPEED_BP=[0.0, 5.0, 20.0],
             PID_JERK_UPPER_V=[2.0, 3.0, 2.0], PID_JERK_LOWER_V=[3.5, 3.5, 3.0],
             clip=lambda x, lo, hi: max(lo, min(x, hi)),
             interp=lambda x, bp, values: values[0], apply_deadzone=lambda x, dz: x,
             long_control_state_trans=lambda *args: (state, False))
  exec(compile(ast.Module(body=[update], type_ignores=[]), str(source), 'exec'), env)
  pid = NS(update=lambda *a, **kw: requested, p=requested, i=0.0, d=0.0, f=0.0)
  obj = NS(_read_params=lambda: None,
           _reset_standstill_lead=lambda: None,
           _update_standstill_lead=lambda *a: False,
           CP=NS(stoppingControl=True, stopAccel=-0.6, vEgoStarting=0.3,
                 longitudinalTuning=NS(deadzoneBP=[0], deadzoneV=[0])),
           actuator_delay_lower=0.2, actuator_delay_upper=0.4, pid=pid,
           long_control_state=state, last_output_accel=previous,
           stopping_decel_rate=1.0, standstill_hold_accel=-1.1,
           pid_jerk_accel_mult=1.0, pid_jerk_decel_mult=1.0, start_jerk=5.0,
           standstill_hold_active=hold_active,
           start_request_frames=0, standstill_release_speed=0.2,
           standstill_release_frames=10, standstill_lead_latched=False,
           lead_missing_frames=0,
           reset=lambda *a: None)
  speed = (0.0 if state == 'stopping' else 10.0) if v_ego is None else v_ego
  cs = NS(vEgo=speed, standstill=speed < 0.01, brakePressed=brake_pressed,
          gasPressed=False, buttonEvents=[],
          cruiseState=NS(standstill=False))
  plan = NS(speeds=[10.0, 10.0], accels=[0.0, 0.0], jerks=[0.0])
  return env['update'](obj, True, cs, plan, (-3.5, 2.0), 0.0)[0]


def test_pid_output_is_jerk_limited_per_cycle():
  # Mock interp always returns values[0]: jerk_upper=2.0, jerk_lower=3.5 m/s^3.
  # Max change per 0.01s cycle: +0.02 (accel) / -0.035 (decel).
  assert brake_output(0.0, -3.0) == -0.035
  assert brake_output(0.0, 3.0) == 0.02
  # Requests within the jerk budget for this cycle pass through unchanged.
  assert brake_output(0.0, -0.03) == -0.03
  assert brake_output(0.0, 0.015) == 0.015


def test_actuator_limits_still_apply():
  # Already close enough to the limit that the jerk budget does not block
  # reaching it in one cycle.
  assert brake_output(-3.49, -5.0) == -3.5
  assert brake_output(1.99, 3.0) == 2.0


def test_standstill_hold_strengthens_only_after_actual_stop():
  assert brake_output(-0.6, 0.0, 'stopping', v_ego=0.2) == -0.6
  assert brake_output(-0.6, 0.0, 'stopping', v_ego=0.0) < -0.6
  assert brake_output(-1.1, 0.0, 'stopping') == -1.1


def test_latched_hold_survives_small_wheel_speed_and_respects_driver_brake():
  assert brake_output(-0.6, 0.0, 'stopping', v_ego=0.1, hold_active=True) < -0.6
  assert brake_output(-0.6, 0.0, 'stopping', v_ego=0.0,
                      brake_pressed=True) == -0.6
