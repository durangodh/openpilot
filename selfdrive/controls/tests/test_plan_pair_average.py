#!/usr/bin/env python3
import ast
from pathlib import Path

import pytest


def _load():
  # drive_helpers imports compiled modules that are not built in CI; load only the class.
  source = Path(__file__).resolve().parents[1] / 'lib' / 'drive_helpers.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  keep = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'PlanPairAverage']
  env = {}
  exec(compile(ast.Module(body=keep, type_ignores=[]), str(source), 'exec'), env)
  return env['PlanPairAverage']


PlanPairAverage = _load()


def test_alternating_plans_cancel():
  # 2026-10-06 lat_trace 176.1-176.3 s: desired curvature per plan
  avg = PlanPairAverage()
  out = [avg.update(c, 0.0, True)[0] for c in (-0.00071, -0.00004, -0.00095, -0.00003)]
  assert out[0] == pytest.approx(-0.00071)
  assert max(out[1:]) - min(out[1:]) < 0.0002     # was 0.0009 apart


def test_holds_between_plan_updates_and_passes_through_when_inactive():
  avg = PlanPairAverage()
  avg.update(0.002, 0.01, True)
  assert avg.update(0.004, 0.03, True) == pytest.approx((0.003, 0.02))
  assert avg.update(0.005, 0.03, False) == pytest.approx((0.0035, 0.02))  # same plan, newer speed
  # Inactive: raw value passes through, but the latest plan value is still tracked
  # every frame, so the first active plan averages with the plan just before it.
  assert avg.update(0.006, 0.0, True, active=False) == (0.006, 0.0)
  assert avg.update(0.002, 0.0, True) == pytest.approx((0.004, 0.0))


def test_step_reaches_new_value_after_one_plan():
  avg = PlanPairAverage()
  for _ in range(3):
    avg.update(0.0, 0.0, True)
  assert avg.update(0.01, 0.0, True)[0] == pytest.approx(0.005)
  assert avg.update(0.01, 0.0, True)[0] == pytest.approx(0.01)
