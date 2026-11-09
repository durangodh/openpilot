"""High-angle reset pulse only while the wheel is nearly still (MDPS fault log 2026-11-09)."""
import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'carcontroller.py'


def load():
  tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
  keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == 'high_angle_pulse_allowed') or
          (isinstance(n, ast.Assign) and any(getattr(t, 'id', '') == 'HIGH_ANGLE_PULSE_MAX_RATE' for t in n.targets))]
  env = {}
  exec(compile(ast.Module(body=keep, type_ignores=[]), str(SOURCE), 'exec'), env)
  return env['high_angle_pulse_allowed']


def test_pulses_that_recovered_on_11_09_are_still_sent():
  allowed = load()
  for rate in (40.0, 0.0, 0.0, 12.0):
    assert allowed(rate)


def test_pulse_that_latched_the_fault_is_not_sent():
  allowed = load()
  assert not allowed(84.0)
  assert not allowed(-84.0)


def test_fast_wheel_releases_torque_until_back_under_the_angle():
  text = SOURCE.read_text(encoding='utf-8')
  assert "if high_angle_pulse_allowed(CS.out.steeringRateDeg):" in text
  assert "self.high_angle_release = True" in text
  release = text.index("if self.high_angle_release:")
  zero = text.index("if not lkas_active:\n      apply_steer = 0")
  assert release < zero   # torque is zeroed in the same frame the release applies
