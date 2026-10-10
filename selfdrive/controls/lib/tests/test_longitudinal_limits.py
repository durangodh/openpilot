import pytest

DT_CTRL = 0.01

from common.conversions import Conversions as CV
from selfdrive.controls.lib.longitudinal_limits import (AUTO_SPEED_UP_RATE_KPH_S,
                                                        CRUISE_MAX_VAL_DEFAULTS,
                                                        get_auto_speed_up_target,
                                                        get_cruise_max_accel,
                                                        get_no_lead_cruise_accel_cap,
                                                        limit_accel_in_turns,
                                                        select_auto_driving_mode)


def test_cruise_max_modes_share_one_policy():
  v_ego = 40.0 * CV.KPH_TO_MS
  assert get_cruise_max_accel(v_ego, CRUISE_MAX_VAL_DEFAULTS, 3) == pytest.approx(1.20)
  assert get_cruise_max_accel(v_ego, CRUISE_MAX_VAL_DEFAULTS, 4) == pytest.approx(1.20)
  assert get_cruise_max_accel(v_ego, CRUISE_MAX_VAL_DEFAULTS, 2, 0.8, 0.9) == pytest.approx(0.96)
  assert get_cruise_max_accel(v_ego, CRUISE_MAX_VAL_DEFAULTS, 1, 0.8, 0.8) == pytest.approx(0.768)


def test_cruise_max_has_dedicated_20_kph_breakpoint():
  assert get_cruise_max_accel(20.0 * CV.KPH_TO_MS, CRUISE_MAX_VAL_DEFAULTS, 3) == pytest.approx(1.00)
  custom = list(CRUISE_MAX_VAL_DEFAULTS)
  custom[1] = 0.90
  assert get_cruise_max_accel(20.0 * CV.KPH_TO_MS, custom, 3) == pytest.approx(0.90)


def test_auto_mode_uses_safe_not_eco_and_can_return_to_normal():
  assert select_auto_driving_mode(5, 3, 90.0) == 1
  assert select_auto_driving_mode(5, 1, 10.0) == 3
  assert select_auto_driving_mode(5, 2, 90.0) == 2
  assert select_auto_driving_mode(5, 4, 10.0) == 4


def test_auto_speed_up_is_rate_limited_and_bounded():
  target = get_auto_speed_up_target(70.0, 100.0, dt=0.01)
  assert target == pytest.approx(70.0 + AUTO_SPEED_UP_RATE_KPH_S * 0.01)
  assert get_auto_speed_up_target(144.99, 300.0, dt=1.0) == 145.0


def test_cruise_max_tracks_the_live_slider_and_driving_mode():
  vals = list(CRUISE_MAX_VAL_DEFAULTS)
  v_ego = 40.0 * CV.KPH_TO_MS
  # read_cruise_params() rewrites cruise_max_vals once a second.
  vals[2] = 0.60
  assert get_cruise_max_accel(v_ego, vals, 3) == pytest.approx(0.60)
  # ECO multiplies the same table by MyEcoModeFactor.
  assert get_cruise_max_accel(v_ego, vals, 2, 0.8) == pytest.approx(0.48)


def test_no_lead_cap_scales_cruise_max_over_the_whole_range():
  assert get_no_lead_cruise_accel_cap(1.2) == pytest.approx(1.2)  # 100% = unchanged
  assert get_no_lead_cruise_accel_cap(1.0, 0.95) == pytest.approx(0.95)
  assert get_no_lead_cruise_accel_cap(1.0, 1.05) == pytest.approx(1.05)
  assert get_no_lead_cruise_accel_cap(1.0, 3.0) == pytest.approx(1.5)  # clipped


def test_turn_limit_preserves_straight_road_acceleration():
  limits = limit_accel_in_turns(30.0, 0.0, [-1.2, 1.2], 15.0, 2.7)
  assert limits == pytest.approx([-1.2, 1.2])


def test_turn_limit_reduces_only_positive_acceleration():
  limits = limit_accel_in_turns(30.0, 10.0, [-1.2, 1.2], 15.0, 2.7)
  assert limits[0] == pytest.approx(-1.2)
  assert limits[1] == pytest.approx(0.0)

  braking_limits = limit_accel_in_turns(30.0, 10.0, [-1.2, -0.2], 15.0, 2.7)
  assert braking_limits == pytest.approx([-1.2, -0.2])

