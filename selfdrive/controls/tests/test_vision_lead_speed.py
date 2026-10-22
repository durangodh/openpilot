#!/usr/bin/env python3
import ast
from collections import deque
from pathlib import Path

import numpy as np


def _load():
  # radard imports compiled modules that are not built in CI; load only the helper.
  source = Path(__file__).resolve().parents[1] / 'radard.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  keep = [n for n in tree.body if (isinstance(n, ast.ClassDef) and n.name == 'VisionLeadSpeed') or
          (isinstance(n, ast.Assign) and any(getattr(t, 'id', '').startswith('VISION_SPEED_') for t in n.targets))]
  env = {'deque': deque, 'np': np}
  exec(compile(ast.Module(body=keep, type_ignores=[]), str(source), 'exec'), env)
  return env['VisionLeadSpeed']


VisionLeadSpeed = _load()


def _vision(d, v_lead, v_ego):
  return {'status': True, 'radar': False, 'dRel': d, 'vLead': v_lead, 'vLeadK': v_lead, 'vRel': v_lead - v_ego}


def test_stopped_car_seen_as_moving_is_slowed_down():
  # 2026-10-17 1846 s: camera says ~6 m/s, but the gap closes at the ego speed
  vls = VisionLeadSpeed()
  out = None
  for i in range(40):  # 2 s at 20 Hz
    t = i * 0.05
    out = vls.update(_vision(90. - 15.5 * t, 6.5, 15.5), t, 15.5)
  assert out['vLead'] < 1.0
  assert abs(out['vRel'] + 15.5 - out['vLead']) < 1e-6


def test_moving_lead_with_noisy_distance_is_left_alone():
  # 2026-10-17 2275 s: lead at 17 m/s, vision distance jumping +-5 m
  vls = VisionLeadSpeed()
  rng = np.random.default_rng(0)
  out = None
  for i in range(40):
    out = vls.update(_vision(80. + rng.choice([-6., 6.]), 17., 16.), i * 0.05, 16.)
  assert out['vLead'] == 17.


def test_radar_lead_and_short_history_are_untouched():
  vls = VisionLeadSpeed()
  radar = {'status': True, 'radar': True, 'dRel': 50., 'vLead': 10., 'vLeadK': 10., 'vRel': -5.}
  assert vls.update(dict(radar), 0., 15.)['vLead'] == 10.
  out = None
  for i in range(10):  # 0.5 s only
    out = vls.update(_vision(90. - 15. * i * 0.05, 6., 15.), i * 0.05, 15.)
  assert out['vLead'] == 6.


def test_never_raises_the_lead_speed():
  vls = VisionLeadSpeed()
  out = None
  for i in range(40):
    out = vls.update(_vision(40. + 10. * i * 0.05, 5., 10.), i * 0.05, 10.)  # pulling away fast
  assert out['vLead'] == 5.


def test_close_cut_in_is_left_alone():
  # 2026-10-20 857 s: cut-in car, camera distance converging 27 -> 15 m while it drives ~13 m/s
  vls = VisionLeadSpeed()
  out = None
  for i in range(30):
    t = i * 0.05
    out = vls.update(_vision(27. - 9. * t, 12.2, 14.9), t, 14.9)
  assert out['vLead'] == 12.2


def _load_prefer():
  source = Path(__file__).resolve().parents[1] / 'radard.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name == 'prefer_nearer_vision') or
          (isinstance(n, ast.Assign) and any(getattr(t, 'id', '').startswith('VISION_NEAR_') for t in n.targets))]
  env = {}
  exec(compile(ast.Module(body=keep, type_ignores=[]), str(source), 'exec'), env)
  return env['prefer_nearer_vision']


prefer_nearer_vision = _load_prefer()


def _lead(d, v):
  return {'status': True, 'dRel': d, 'vLead': v}


def test_nearer_slow_camera_lead_replaces_far_radar_lead():
  # 2026-10-22 420 s: radar 49 m, camera 36 m at ~2 m/s while we drive 10 m/s
  assert prefer_nearer_vision(_lead(49., 2.), _lead(36., 1.5), 10., 0.9)


def test_radar_kept_when_close_agreeing_fast_far_or_uncertain():
  assert not prefer_nearer_vision(_lead(40., 9.), _lead(37., 2.), 10., 0.9)   # within 5 m
  assert not prefer_nearer_vision(_lead(49., 9.), _lead(36., 9.5), 10., 0.9)  # camera lead not slower
  assert not prefer_nearer_vision(_lead(90., 2.), _lead(70., 2.), 20., 0.9)   # camera too far to trust
  assert not prefer_nearer_vision(_lead(49., 2.), _lead(36., 2.), 10., 0.6)   # low model prob
  assert not prefer_nearer_vision({'status': False}, _lead(36., 2.), 10., 0.9)
