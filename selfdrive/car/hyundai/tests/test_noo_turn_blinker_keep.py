"""NOO turn keeps steering through the low-speed blinker cut (no CAN imports)."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS


def load_gate():
  source = Path(__file__).resolve().parents[1] / 'carcontroller.py'
  tree = ast.parse(source.read_text())
  cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'CarController')
  function = next(node for node in cls.body
                  if isinstance(node, ast.FunctionDef) and node.name == '_noo_turn_matches_blinker')
  function.decorator_list = []
  env = {}
  exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), env)
  return env['_noo_turn_matches_blinker']


def state(left=False, right=False):
  return NS(out=NS(leftBlinker=left, rightBlinker=right))


def controls(noo_turn):
  return NS(sm={'lateralPlan': NS(nooTurnDirection=noo_turn)})


def test_matching_blinker_keeps_steering():
  gate = load_gate()
  assert gate(state(left=True), controls(-1))
  assert gate(state(right=True), controls(1))


def test_blinker_off_hold_keeps_steering_during_noo_turn():
  assert load_gate()(state(), controls(-1))


def test_opposite_blinker_or_no_noo_turn_keeps_the_cut():
  gate = load_gate()
  assert not gate(state(right=True), controls(-1))
  assert not gate(state(left=True), controls(1))
  assert not gate(state(left=True), controls(0))


def test_missing_lateral_plan_fails_closed():
  assert not load_gate()(state(left=True), NS(sm={}))
