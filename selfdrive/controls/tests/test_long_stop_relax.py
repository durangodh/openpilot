#!/usr/bin/env python3
import ast
from pathlib import Path
from types import SimpleNamespace as NS


def _load_relax():
  source = Path(__file__).resolve().parents[1] / 'lib' / 'longcontrol.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LongControl')
  fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_relax_stop_overbrake')
  fn.decorator_list = []
  env = {'DT_CTRL': 0.01}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), str(source), 'exec'), env)
  return env['_relax_stop_overbrake']


relax = _load_relax()


def test_stopping_relaxes_overbrake_toward_stop_accel_while_rolling():
  # 2026-10-19: STOPPING ACCEL 15 (-0.30) but stopping held -0.52 below 2 km/h.
  cs = NS(vEgo=0.5, standstill=False)
  out = -0.52
  for _ in range(10):
    out = relax(out, -0.30, 1.2, cs, 0.3)
  assert abs(out - (-0.40)) < 1e-6
  for _ in range(100):
    out = relax(out, -0.30, 1.2, cs, 0.3)
  assert out == -0.30


def test_stopping_relax_leaves_weaker_standstill_and_fast_cases():
  assert relax(-0.20, -0.30, 1.2, NS(vEgo=0.5, standstill=False), 0.3) == -0.20
  assert relax(-0.52, -0.30, 1.2, NS(vEgo=0.0, standstill=True), 0.3) == -0.52
  assert relax(-0.90, -0.30, 1.2, NS(vEgo=3.0, standstill=False), 0.3) == -0.90
