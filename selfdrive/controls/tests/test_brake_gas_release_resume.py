#!/usr/bin/env python3
import ast
from pathlib import Path
from types import MethodType, SimpleNamespace as NS

import pytest


def _load():
  source = Path(__file__).resolve().parents[1] / 'lib' / 'cruise_helper.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CruiseHelper')
  methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and
             n.name in ('_resume_path_clear', '_resume_after_brake_gas_release')]
  env = {'DT_CTRL': 0.01, 'BRAKE_GAS_RESUME_WINDOW': 15.0}
  exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])), str(source), 'exec'), env)
  return env['_resume_path_clear'], env['_resume_after_brake_gas_release']


_path_clear, _resume = _load()


def _case(**kw):
  helper = NS(brake_gas_resume_pending=True, brake_gas_resume_frame=100,
              param_read_counter=800, long_active_user=-2,
              auto_cruise_control=True, user_cruise_paused=False,
              auto_resume_from_gas=1, auto_resume_from_brake_release=False,
              auto_gas_resume_guard=True, resumed=[])
  helper.__dict__.update(kw)
  helper._resume_path_clear = MethodType(_path_clear, helper)
  helper._resume_longitudinal = lambda controls, cs, mode: helper.resumed.append(mode)
  controls = NS(enabled=True, sm=NS(valid={'radarState': True}, alive={'radarState': True}))
  cs = NS(vEgo=8.0, brakePressed=False, steeringAngleDeg=0.0,
          leftBlinker=False, rightBlinker=False)
  lead = NS(status=True, radar=True, dRel=16.0)
  return helper, controls, cs, lead


def test_brake_then_gas_release_resumes_with_safe_radar_lead():
  helper, controls, cs, lead = _case()
  assert _resume(helper, controls, cs, lead)
  assert helper.resumed == [3]
  assert not helper.brake_gas_resume_pending


@pytest.mark.parametrize('veto', ['disabled', 'settings', 'cancelled', 'stale', 'radar_invalid', 'vision',
                                  'no_lead', 'close_lead', 'far_lead',
                                  'brake', 'steering', 'blinker'])
def test_brake_then_gas_release_resume_is_one_shot_and_guarded(veto):
  helper, controls, cs, lead = _case()
  if veto == 'disabled':
    controls.enabled = False
  elif veto == 'settings':
    helper.auto_resume_from_gas = 0
    helper.auto_resume_from_brake_release = False
  elif veto == 'cancelled':
    helper.long_active_user = 0
    helper.user_cruise_paused = True
  elif veto == 'stale':
    helper.param_read_counter = 2000
  elif veto == 'radar_invalid':
    controls.sm.valid['radarState'] = False
  elif veto == 'vision':
    lead.radar = False
  elif veto == 'no_lead':
    lead = None
  elif veto == 'close_lead':
    lead.dRel = 5.0
  elif veto == 'far_lead':
    lead.dRel = 61.0
  elif veto == 'brake':
    cs.brakePressed = True
  elif veto == 'steering':
    cs.steeringAngleDeg = 20.0
  elif veto == 'blinker':
    cs.leftBlinker = True

  assert not _resume(helper, controls, cs, lead)
  assert helper.resumed == []
  assert not helper.brake_gas_resume_pending
