"""구간단속 보상 가속: average below the limit raises the section target."""
import ast
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from selfdrive.eon_cluster.section_average import SectionAverage, section_boost_speed  # noqa: E402

KPH_TO_MS = 1 / 3.6


def load_helper():
  """Build a CruiseHelper stand-in with only the section boost methods."""
  source = Path(__file__).resolve().parents[1] / 'cruise_helper.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'CruiseHelper')
  methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and
             n.name in ('section_boost_target', 'calculate_navi_speed')]
  consts = [n for n in tree.body if isinstance(n, ast.Assign) and
            any(getattr(t, 'id', '') == 'SECTION_BOOST_AVG_MARGIN_KPH' for t in n.targets)]
  fake = ast.ClassDef(name='Helper', bases=[], keywords=[], body=methods, decorator_list=[])
  module = ast.fix_missing_locations(ast.Module(body=consts + [fake], type_ignores=[]))

  class CV:
    KPH_TO_MS = KPH_TO_MS
    MS_TO_KPH = 3.6

  env = {'np': np, 'CV': CV, 'section_boost_speed': section_boost_speed}
  exec(compile(module, str(source), 'exec'), env)
  helper = env['Helper']()
  helper.auto_navi_section_boost = 10.0
  helper.auto_navi_speed_safety_factor = 1.05
  helper.auto_navi_speed_ctrl_end = 7.0
  helper.auto_navi_speed_decel_rate = 0.8
  helper.section_boost_kph = 0.0
  helper.section_average = SectionAverage()
  return helper


def test_required_speed_closes_the_gap():
  # 120 s at 70 km/h, target 98: needed speed over the remaining 5 km.
  v = section_boost_speed(70.0, 120.0, 5000.0, 98.0)
  driven = 70.0 / 3.6 * 120.0
  total_time = 120.0 + 5000.0 / (v / 3.6)
  assert abs((driven + 5000.0) / total_time * 3.6 - 98.0) < 1e-6
  assert v > 98.0


def test_no_boost_when_on_target_or_too_early():
  assert section_boost_speed(98.0, 120.0, 5000.0, 98.0) == 0.0
  assert section_boost_speed(0.0, 120.0, 5000.0, 98.0) == 0.0
  assert section_boost_speed(70.0, 5.0, 5000.0, 98.0) == 0.0


def test_target_raised_but_capped():
  helper = load_helper()
  helper.section_average.started_at = 0.0
  # Far from the exit: boost capped at base + 10.
  out = helper.section_boost_target(105.0, 100.0, 60.0, 3000.0, 300.0)
  assert out == 115.0
  assert helper.section_boost_kph == 10.0


def test_no_boost_near_exit_or_when_disabled():
  helper = load_helper()
  helper.section_average.started_at = 0.0
  # 50 m left: must already be back at the base target for the exit camera.
  assert helper.section_boost_target(105.0, 100.0, 60.0, 50.0, 300.0) == 105.0
  # Average already at the limit.
  assert helper.section_boost_target(105.0, 100.0, 99.0, 3000.0, 300.0) == 105.0
  helper.auto_navi_section_boost = 0.0
  assert helper.section_boost_target(105.0, 100.0, 60.0, 3000.0, 300.0) == 105.0
