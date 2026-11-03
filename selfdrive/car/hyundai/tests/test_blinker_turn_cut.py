"""Manual blinker turns must drop steering torque (DH MDPS fault near 90 deg)."""
import ast
from pathlib import Path


def load():
  source = Path(__file__).resolve().parents[1] / 'carcontroller.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == 'blinker_turn_cut') or
          (isinstance(n, ast.Assign) and n.targets[0].id.startswith('BLINKER_TURN_'))]
  env = {}
  exec(compile(ast.Module(body=keep, type_ignores=[]), str(source), 'exec'), env)
  return env['blinker_turn_cut']


cut = load()


def test_intersection_turn_cuts_and_releases_after_return():
  latched = cut(False, True, 50.0, False, 5.0)
  assert latched
  assert cut(latched, False, 60.0, False, 5.0)        # blinker off, wheel still turned
  assert not cut(latched, False, 10.0, False, 5.0)    # back near center


def test_driver_steering_at_low_speed_cuts():
  assert cut(False, True, 15.0, True, 20 / 3.6)


def test_highway_lane_change_keeps_steering():
  assert not cut(False, True, 3.0, True, 90 / 3.6)    # nudge to confirm lane change
  assert not cut(False, True, 3.0, False, 90 / 3.6)


def test_no_blinker_no_cut():
  assert not cut(False, False, 60.0, True, 5.0)
