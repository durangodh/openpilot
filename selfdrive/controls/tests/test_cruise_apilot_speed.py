#!/usr/bin/env python3
"""aPilot c2-master cruise eco overspeed and curve-speed table ported into CruiseHelper."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np

from common.filter_simple import StreamingMovingAverage
from common.numpy_fast import clip, interp

SOURCE = Path(__file__).resolve().parents[1] / 'lib' / 'cruise_helper.py'


def _load(*names):
  tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
  consts = [n for n in tree.body if isinstance(n, ast.Assign) and
            any(isinstance(t, ast.Name) and t.id.startswith('AP_CURVE') for t in n.targets)]
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CruiseHelper')
  fns = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
  env = {'np': np, 'clip': clip, 'interp': interp, 'UNLIMITED_SPEED': 250.0 / 3.6,
         'CV': NS(KPH_TO_MS=1 / 3.6, MS_TO_KPH=3.6)}
  exec(compile(ast.fix_missing_locations(ast.Module(body=consts + fns, type_ignores=[])), str(SOURCE), 'exec'), env)
  return env


ENV = _load('cruise_control_speed', 'cal_curve_speed_apilot')


def _eco(mode=4.0, active=1):
  h = NS(cruise_control_mode=mode, long_active_user=active, cruise_speed_target=0.0, lead_speed_up_active=False)
  return h, lambda v_cruise, v_ego: ENV['cruise_control_speed'](h, v_cruise, v_ego)


def test_eco_overspeed_until_set_speed_reached():
  h, run = _eco()
  assert run(80, 70) == 84          # 3 km/h 넘게 느림 -> 설정 + 4
  assert run(80, 79) == 84          # 도달 전까지 유지
  assert run(80, 80) == 84
  assert run(80, 81) == 80          # 설정속도 초과 -> 종료
  assert h.cruise_speed_target == 0
  assert run(80, 78) == 80          # 3 km/h 이내 감속은 재시작하지 않음


def test_eco_follows_set_speed_changes_and_off_states():
  h, run = _eco()
  assert run(80, 60) == 84
  assert run(90, 60) == 94          # 설정 증가 -> 따라감
  assert run(70, 60) == 70          # 설정 감소 -> 종료
  _, run_off = _eco(mode=0)
  assert run_off(80, 60) == 80
  _, run_paused = _eco(active=-2)
  assert run_paused(80, 60) == 80
  h, run = _eco()
  h.lead_speed_up_active = True     # aPilot: 앞차 증속 중에는 증속된 설정속도 그대로
  assert run(80, 60) == 80


def _curve(rate, v_ego, frames=40, factor=1.0, factor_in=0.1, lower=30.0):
  h = NS(ap_curve_filter=StreamingMovingAverage(20), ap_turn_speed_prev=300.,
         auto_curve_speed_factor=factor, auto_curve_speed_factor_in=factor_in,
         auto_curve_speed_lower_limit=lower)
  sm = {'modelV2': NS(orientationRate=NS(z=[rate] * 33))}
  out = None
  for _ in range(frames):
    out = ENV['cal_curve_speed_apilot'](h, sm, v_ego)
  return out * 3.6


def test_curve_straight_is_unlimited():
  assert _curve(0.0, 25.0) > 249


def test_curve_table_and_entry_factor():
  # 회전율 / 현재속도 = 1/190 (표 70 km/h). aPilot 은 다음 프레임부터 곡률을 직전
  # 통과속도로 다시 나눠 더 크게 보고, 진입 속도 초과분 x 10% 를 더 뺀다.
  v = 90 / 3.6
  speed = _curve(v / 190., v)
  assert 50 < speed < 65
  assert _curve(v / 190., v, factor_in=0.0) > speed
  assert abs(_curve(v / 15., v) - 30.0) < 1e-6  # 최저속도 설정 아래로 내려가지 않음
