#!/usr/bin/env python3
import ast
import math
from pathlib import Path
from types import SimpleNamespace


def _load():
  source = Path(__file__).resolve().parents[1] / 'radard.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  keep = [n for n in tree.body
          if (isinstance(n, ast.FunctionDef) and n.name in ('laplacian_cdf', 'match_vision_to_cluster')) or
          (isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in n.targets))]
  env = {'math': math, 'RADAR_TO_CAMERA': 1.52}
  exec(compile(ast.fix_missing_locations(ast.Module(body=keep, type_ignores=[])), str(source), 'exec'), env)
  return env


_env = _load()
RADAR_TO_CAMERA = _env['RADAR_TO_CAMERA']
match_vision_to_cluster = _env['match_vision_to_cluster']


def _lead(d, v, y=0.0):
  return SimpleNamespace(x=[d + RADAR_TO_CAMERA], xStd=[2.0], y=[y], yStd=[0.5], v=[v], vStd=[1.0])


def _cluster(d, v_lead, v_ego, y=0.0):
  return SimpleNamespace(dRel=d, yRel=y, vRel=v_lead - v_ego)


def test_far_radar_object_rejected_when_closing():
  # 2026-10-04 drive: stopped car seen by vision at 48.5 m, SCC radar on a moving object at 61 m
  v_ego = 16.5
  assert match_vision_to_cluster(v_ego, _lead(48.5, 7.0), [_cluster(60.9, 4.4, v_ego)], scc_only=True) is None


def test_near_radar_object_still_matched_when_closing():
  v_ego = 16.5
  c = _cluster(44.0, 0.5, v_ego)
  assert match_vision_to_cluster(v_ego, _lead(48.5, 7.0), [c], scc_only=True) is c


def test_small_far_error_still_matched_when_closing():
  v_ego = 16.5
  c = _cluster(54.0, 6.0, v_ego)
  assert match_vision_to_cluster(v_ego, _lead(48.5, 7.0), [c], scc_only=True) is c


def test_steady_following_keeps_wide_tolerance():
  v_ego = 20.0
  c = _cluster(60.0, 20.0, v_ego)
  assert match_vision_to_cluster(v_ego, _lead(48.5, 20.0), [c], scc_only=True) is c
