"""Regression checks for the apilot-c2 LongControl brake-feedback path."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS


SOURCE = Path(__file__).parents[1] / 'lib' / 'longcontrol.py'
TEXT = SOURCE.read_text(encoding='utf-8')
TREE = ast.parse(TEXT)


def test_pid_limits_only_feedback_brake_rise():
  assert "output_accel = self.pid.f + feedback_limited" in TEXT
  assert "limit_brake_feedback(feedback_target, self.last_pid_feedback" in TEXT
  assert "self.pid.f =" not in TEXT
  assert "pos_allowance" not in TEXT
  assert "NO_LEAD_ALLOWANCE_RISE" not in TEXT


def test_feedback_limiter_prevents_integral_windup():
  assert "previous_i = self.pid.i" in TEXT
  assert "self.pid.i = previous_i" in TEXT


def load_feedback_limiter():
  names = {'FEEDBACK_BRAKE_JERK_BASE', 'FEEDBACK_BRAKE_JERK_KP',
           'FEEDBACK_BRAKE_JERK_MIN', 'FEEDBACK_BRAKE_JERK_MAX'}
  nodes = [node for node in TREE.body
           if (isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names
                                                    for target in node.targets)) or
              (isinstance(node, ast.FunctionDef) and node.name == 'limit_brake_feedback')]
  env = dict(DT_CTRL=0.01, clip=lambda value, lower, upper: max(lower, min(value, upper)))
  exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), env)
  return env['limit_brake_feedback']


def test_default_kp_limits_extra_brake_to_two_meters_per_second_cubed():
  limit_feedback = load_feedback_limiter()
  assert limit_feedback(-1.3, 0.0, 1.0, dt=0.05) == -0.1


def test_feedback_brake_release_is_not_delayed():
  limit_feedback = load_feedback_limiter()
  assert limit_feedback(0.2, -0.8, 1.0, dt=0.05) == 0.2


def test_positive_feedback_never_cancels_a_new_planner_brake_request():
  limit_feedback = load_feedback_limiter()
  assert limit_feedback(-1.3, 0.8, 1.0, dt=0.05) == -0.1


def test_pid_does_not_keep_previous_launch_limiter_frozen():
  assert "freeze_integrator = prevent_overshoot" in TEXT
  assert "prevent_overshoot or self.launch_limited" not in TEXT


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
