#!/usr/bin/env python3
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest


def _load():
  source = Path(__file__).resolve().parents[1] / 'lib' / 'cruise_helper.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CruiseHelper')
  fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_brake_release_resume')
  env = {'CV': NS(MS_TO_KPH=3.6), 'DT_CTRL': 0.01, 'XState': NS(softHold='softHold', cruise='cruise')}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), str(source), 'exec'), env)
  return env['_brake_release_resume']


_resume = _load()


def _helper(**kw):
  h = NS(auto_cruise_control=True, x_state='cruise', auto_resume_from_brake_release=True,
         auto_resume_from_brake_car_speed=20.0, auto_resume_from_brake_release_dist=10.0,
         auto_resume_from_gas_speed=20.0, param_read_counter=1000, gas_pressed_frame=0,
         slow_speed_frame_count=0, d_rel=5.2, traffic_state=0, resumed=[])
  h.__dict__.update(kw)
  h._resume_longitudinal = lambda controls, CS, mode: h.resumed.append(mode)
  h._select_resume_speed = lambda controls, CS: None
  return h


def _cs(kph):
  return NS(vEgoCluster=kph / 3.6, steeringAngleDeg=0.0, leftBlinker=False, rightBlinker=False)


def test_no_resume_below_brake_car_speed_even_behind_close_lead():
  # 2026-10-06 log 1276 s: stopped 5.2 m behind a lead, brake released -> resumed.
  h = _helper()
  _resume(h, NS(), _cs(0.0))
  assert h.resumed == []


@pytest.mark.parametrize('d_rel', [0.0, 30.0])
def test_resume_above_brake_car_speed(d_rel):
  h = _helper(d_rel=d_rel, auto_resume_from_brake_release_dist=10.0)
  _resume(h, NS(), _cs(40.0))
  assert h.resumed == [3]
