"""Test the StopReq gate without importing cereal or CAN dependencies."""
import ast
from pathlib import Path


def load_stop_request_gate():
  source = Path(__file__).resolve().parents[1] / 'carcontroller.py'
  tree = ast.parse(source.read_text())
  function = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == 'should_request_scc_standstill')
  env = {}
  exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), env)
  return env['should_request_scc_standstill']


def test_stopping_does_not_request_stop_while_vehicle_is_moving():
  gate = load_stop_request_gate()
  assert not gate(True, False, False, 1.9)
  assert not gate(True, False, False, 0.1)


def test_stopping_requests_stop_at_actual_standstill():
  gate = load_stop_request_gate()
  assert gate(True, False, True, 0.2)
  assert gate(True, False, False, 0.09)


def test_soft_hold_uses_the_same_actual_standstill_gate():
  gate = load_stop_request_gate()
  assert not gate(False, True, False, 0.5)
  assert gate(False, True, True, 0.0)


def test_inactive_state_never_requests_stop():
  gate = load_stop_request_gate()
  assert not gate(False, False, True, 0.0)
