import ast
from pathlib import Path

import numpy as np
import pytest

from common.numpy_fast import interp


SOURCE = Path(__file__).parents[1] / 'drive_helpers.py'
module = ast.parse(SOURCE.read_text())
function = next(node for node in module.body
                if isinstance(node, ast.FunctionDef) and node.name == '_curve_entry_extra_delay')
env = dict(np=np, interp=interp, CURVE_ENTRY_EXTRA_DELAY_MAX=.30,
           CURVE_ENTRY_MIN_SPEED=4.0, CURVE_ENTRY_FULL_SPEED=13.0,
           CURVE_ENTRY_ZERO_SPEED=17.0)
exec(compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec'), env)
_curve_entry_extra_delay = env['_curve_entry_extra_delay']

T = np.array([0.0, .01, .04, .09, .16, .24, .35, .48, .62,
              .78, .96, 1.16, 1.38, 1.62, 1.88, 2.16, 2.46])


def test_sharp_curve_entry_gets_bounded_extra_lookahead_in_both_directions():
  entry = np.interp(T, [0, .2, .7, 1.0], [0, 0, .05, .06])
  assert _curve_entry_extra_delay(7.5, entry, T) == pytest.approx(.30)
  assert _curve_entry_extra_delay(7.5, -entry, T) == pytest.approx(.30)


def test_steady_curve_does_not_shift_its_tracking_point():
  assert _curve_entry_extra_delay(7.5, np.full(len(T), .05), T) == 0


def test_straight_small_bend_and_gradual_tightening_keep_normal_delay():
  assert _curve_entry_extra_delay(7.5, np.zeros(len(T)), T) == 0
  assert _curve_entry_extra_delay(7.5, np.linspace(0, .01, len(T)), T) == 0
  gradual = np.interp(T, [0, 1], [.025, .035])
  assert _curve_entry_extra_delay(7.5, gradual, T) == 0


def test_s_bend_and_invalid_prediction_keep_normal_delay():
  s_bend = np.interp(T, [0, .3, .65, 1.0], [0, .05, -.05, -.06])
  assert _curve_entry_extra_delay(7.5, s_bend, T) == 0
  invalid = np.zeros(len(T)); invalid[5] = np.nan
  assert _curve_entry_extra_delay(7.5, invalid, T) == 0


@pytest.mark.parametrize('speed', [0, 3.9, 17, 25])
def test_no_extra_lookahead_outside_bounded_speed_range(speed):
  entry = np.interp(T, [0, .2, .7, 1.0], [0, 0, .05, .06])
  assert _curve_entry_extra_delay(speed, entry, T) == 0


def test_extra_lookahead_fades_at_higher_speed():
  entry = np.interp(T, [0, .2, .7, 1.0], [0, 0, .05, .06])
  assert _curve_entry_extra_delay(15, entry, T) == pytest.approx(.15)
