"""Test the stock HUD wheel state without importing cereal or CAN dependencies."""
import ast
from pathlib import Path


def load():
  source = Path(__file__).resolve().parents[1] / 'carcontroller.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  keep = [node for node in tree.body
          if (isinstance(node, ast.FunctionDef) and node.name == 'hud_wheel_state') or
             (isinstance(node, ast.Assign) and node.targets[0].id.startswith('LKAS_STATE_'))]
  env = {}
  exec(compile(ast.Module(body=keep, type_ignores=[]), str(source), 'exec'), env)
  return env


ENV = load()
hud_wheel_state = ENV['hud_wheel_state']
LKAS_STATE_ACTIVE = ENV['LKAS_STATE_ACTIVE']
LKAS_STATE_STANDBY = ENV['LKAS_STATE_STANDBY']


def test_default_mode_keeps_lane_based_state():
  assert hud_wheel_state(5, True, False, False, 7) == 5
  assert hud_wheel_state(1, False, True, False, 7) == 1


def test_state_mode_gray_green_fault():
  assert hud_wheel_state(1, False, False, True, 7) == LKAS_STATE_STANDBY
  assert hud_wheel_state(1, True, False, True, 7) == LKAS_STATE_ACTIVE
  assert hud_wheel_state(3, True, True, True, 7) == 7
  assert hud_wheel_state(3, False, True, True, 9) == 9


def test_test_value_only_when_not_engaged():
  assert hud_wheel_state(3, False, False, False, 7, test_state=11) == 11
  assert hud_wheel_state(3, True, False, True, 7, test_state=11) == LKAS_STATE_ACTIVE
