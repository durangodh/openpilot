"""SpeedBumpHold: hold the bump speed for a short distance after passing it."""
import ast
from pathlib import Path


def load():
  source = Path(__file__).resolve().parents[1] / 'cruise_helper.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  keep = [n for n in tree.body
          if (isinstance(n, ast.ClassDef) and n.name == 'SpeedBumpHold') or
             (isinstance(n, ast.Assign) and any(getattr(t, 'id', '').startswith('BUMP_') for t in n.targets))]
  env = {}
  exec(compile(ast.Module(body=keep, type_ignores=[]), str(source), 'exec'), env)
  return env


def test_hold_covers_configured_distance_then_releases():
  env = load()
  hold = env['SpeedBumpHold']()
  assert not hold.update(1.0)
  hold.arm()
  traveled, step = 0.0, 10.0 / 3.6 * 0.01  # 10 km/h over the bump
  while hold.update(step):
    traveled += step
  assert abs(traveled - env['BUMP_PASS_HOLD_DIST']) < 0.05


def test_lost_guidance_adds_remaining_distance():
  env = load()
  hold = env['SpeedBumpHold']()
  hold.arm(5.0)
  assert hold.remaining == env['BUMP_PASS_HOLD_DIST'] + 5.0
  hold.reset()
  assert not hold.update(0.0)


def test_constants_are_sane():
  env = load()
  assert env['BUMP_MIN_SAFE_TIME'] >= 1.0
  assert env['BUMP_LOST_ARM_DIST'] > env['BUMP_PASS_HOLD_DIST']
