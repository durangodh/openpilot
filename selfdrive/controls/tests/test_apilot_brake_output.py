"""Regression checks for the simplified apilot-c2 LongControl path."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS


SOURCE = Path(__file__).parents[1] / 'lib' / 'longcontrol.py'
TEXT = SOURCE.read_text()
TREE = ast.parse(TEXT)


def test_pid_executes_one_direct_acceleration_request():
  assert "output_accel = pid_output" in TEXT
  assert "jerk_upper =" not in TEXT
  assert "pos_allowance" not in TEXT
  assert "NO_LEAD_ALLOWANCE_RISE" not in TEXT


def test_output_has_one_final_vehicle_limit_clip():
  assert "self.last_output_accel = clip(output_accel, accel_limits[0], accel_limits[1])" in TEXT


def load_state_transition():
  function = next(node for node in TREE.body
                  if isinstance(node, ast.FunctionDef) and node.name == 'long_control_state_trans')
  env = dict(LongCtrlState=NS(off=0, pid=1, stopping=2, starting=3))
  exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), env)
  return env['long_control_state_trans'], env['LongCtrlState']


def cp():
  return NS(enableGasInterceptor=False, vEgoStopping=0.3,
            vEgoStarting=0.2, startingState=False)


def test_stopped_lead_gate_is_retained_as_a_safety_veto():
  transition, state = load_state_transition()
  result, _ = transition(cp(), True, state.stopping, 0.0, 0.0, 1.0,
                         False, False, False, 0.2, False, False)
  assert result == state.stopping


def test_confirmed_departure_can_return_directly_to_pid():
  transition, state = load_state_transition()
  result, _ = transition(cp(), True, state.stopping, 0.0, 0.0, 1.0,
                         False, False, False, 0.2, True, True)
  assert result == state.pid
