"""A steering request must not start with the wheel past the high angle (2026-11-04)."""
import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "carcontroller.py"
_TREE = ast.parse(_SRC.read_text())
_ENV = {}
exec(compile(ast.Module(body=[n for n in _TREE.body if (isinstance(n, ast.FunctionDef) and
                              n.name == "high_angle_start_gate" or (isinstance(n, ast.Assign) and
                              getattr(n.targets[0], "id", "") == "EXTREME_ANGLE_DEG"))], type_ignores=[]), str(_SRC), "exec"), _ENV)
high_angle_start_gate = _ENV["high_angle_start_gate"]


def step(states, angle, request=True):
  req, hold = high_angle_start_gate(request, states["req"], states["hold"], angle, 85)
  states["req"], states["hold"] = req, hold
  return req


def test_new_request_past_85_waits_until_back_under():
  s = {"req": False, "hold": False}
  assert not step(s, 338)          # engaged at 338 deg: hold
  assert not step(s, 120)
  assert step(s, 60)               # back under 85: start
  assert step(s, 200)              # ongoing request keeps steering at a big angle (<= 240)


def test_request_started_below_keeps_steering_and_off_resets():
  s = {"req": False, "hold": False}
  assert step(s, 10)
  assert step(s, 230)              # turn that began below 85
  assert not step(s, 230, request=False)
  assert not step(s, 230)          # restarted past 85: hold again
  assert step(s, -84)


def test_ongoing_request_drops_past_240_and_restarts_under_85():
  s = {"req": False, "hold": False}
  assert step(s, 60)
  assert step(s, 238)              # big turn begun below 85 keeps steering
  assert not step(s, 241)          # 2026-11-04 faults at 263-289 deg
  assert not step(s, 200)          # still past the high angle: wait
  assert step(s, 80)               # back under 85: steer again
