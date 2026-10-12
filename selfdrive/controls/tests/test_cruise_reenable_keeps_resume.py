#!/usr/bin/env python3
"""SCC re-enable keeps pedal/brake auto-resume armed; first SET/RES arms it, CANCEL/main off clears it."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS


def _load():
  source = Path(__file__).resolve().parents[1] / 'lib' / 'cruise_helper.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CruiseHelper')
  fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'update_cruise_speed')
  env = {'CV': NS(MS_TO_KPH=3.6), 'MAX_SET_SPEED_KPH': 145}
  exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), str(source), 'exec'), env)
  return env['update_cruise_speed']


_update = _load()


def _run(long_active_user, user_cruise_paused):
  h = NS(is_cruise_enabled=False, auto_cruise_control=True, long_active_user=long_active_user,
         user_cruise_paused=user_cruise_paused, speed_from_pcm=2, cruise_speed_min=30, resumed=[])
  h._resume_longitudinal = lambda controls, CS, mode: h.resumed.append(mode)
  controls = NS(enabled=True, v_cruise_kph=70.0)
  CS = NS(cruiseState=NS(speed=70 / 3.6, enabledAcc=True, available=True))
  _update(h, controls, CS, True)
  return h


def test_brake_pause_keeps_auto_resume_armed():
  h = _run(-2, False)
  assert h.auto_cruise_control
  assert h.resumed == []          # no immediate re-engage; pedal/brake paths decide


def test_scc_reenable_never_clears_it_like_c2():
  # C2: SCC state changes do not clear it; CANCEL (pause_longitudinal) does.
  assert _run(-2, True).auto_cruise_control
  assert _run(0, False).auto_cruise_control


def test_first_set_res_required_and_main_off_disarms():
  source = Path(__file__).resolve().parents[1] / 'lib' / 'cruise_helper.py'
  assert 'self.auto_cruise_control = False\n    self.long_active_user = 0' in source.read_text(encoding='utf-8')
  h = _run(3, False)
  _update(h, NS(enabled=True, v_cruise_kph=70.0), NS(cruiseState=NS(speed=0, enabledAcc=False, available=False)), True)
  assert not h.auto_cruise_control and h.long_active_user == 0
