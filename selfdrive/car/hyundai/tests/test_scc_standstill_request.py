"""Test the StopReq gate without importing cereal or CAN dependencies."""
import ast
from pathlib import Path


def load_stop_request_gate():
  source = Path(__file__).resolve().parents[1] / 'carcontroller.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  function = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef) and node.name == 'should_request_scc_standstill')
  env = {}
  exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), env)
  return env['should_request_scc_standstill']


def test_stopping_requests_stop_while_still_rolling_like_apilot():
  gate = load_stop_request_gate()
  assert gate(True, False)


def test_soft_hold_scc_requests_stop():
  gate = load_stop_request_gate()
  assert gate(False, True)


def test_inactive_state_never_requests_stop():
  gate = load_stop_request_gate()
  assert not gate(False, False)
