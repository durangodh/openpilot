import pytest

from common.conversions import Conversions as CV
from selfdrive.controls.lib.longitudinal_limits import (AUTO_SPEED_UP_RATE_KPH_S,
                                                        CRUISE_MAX_VAL_DEFAULTS,
                                                        apply_no_lead_cruise_accel_limit,
                                                        apply_cruise_max_limit,
                                                        get_auto_speed_up_target,
                                                        get_cruise_max_accel,
                                                        get_no_lead_cruise_accel_cap,
                                                        select_auto_driving_mode)


def test_cruise_max_modes_share_one_policy():
  v_ego = 40.0 * CV.KPH_TO_MS
  assert get_cruise_max_accel(v_ego, CRUISE_MAX_VAL_DEFAULTS, 3) == pytest.approx(1.20)
  assert get_cruise_max_accel(v_ego, CRUISE_MAX_VAL_DEFAULTS, 4) == pytest.approx(1.20)
  assert get_cruise_max_accel(v_ego, CRUISE_MAX_VAL_DEFAULTS, 2, 0.8, 0.9) == pytest.approx(0.96)
  assert get_cruise_max_accel(v_ego, CRUISE_MAX_VAL_DEFAULTS, 1, 0.8, 0.8) == pytest.approx(0.768)


def test_cruise_max_has_dedicated_20_kph_breakpoint():
  assert get_cruise_max_accel(20.0 * CV.KPH_TO_MS, CRUISE_MAX_VAL_DEFAULTS, 3) == pytest.approx(1.40)
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


def test_cruise_max_limit_clips_pid_overshoot():
  # Keep the SCC12 transport guard even though LongControl normally uses the
  # same CruiseMax value as its PID positive limit.
  cap = get_cruise_max_accel(40.0 * CV.KPH_TO_MS, CRUISE_MAX_VAL_DEFAULTS, 3)
  assert apply_cruise_max_limit(2.5, False, cap) == pytest.approx(1.20)
  assert apply_cruise_max_limit(0.4, False, cap) == pytest.approx(0.4)


def test_cruise_max_limit_never_weakens_braking_or_stopping():
  cap = get_cruise_max_accel(40.0 * CV.KPH_TO_MS, CRUISE_MAX_VAL_DEFAULTS, 3)
  assert apply_cruise_max_limit(-2.0, False, cap) == pytest.approx(-2.0)
  assert apply_cruise_max_limit(0.0, False, cap) == pytest.approx(0.0)
  assert apply_cruise_max_limit(2.5, True, cap) == pytest.approx(2.5)


def test_cruise_max_limit_tracks_the_live_slider_and_driving_mode():
  vals = list(CRUISE_MAX_VAL_DEFAULTS)
  v_ego = 40.0 * CV.KPH_TO_MS
  # read_cruise_params() rewrites cruise_max_vals once a second.
  vals[2] = 0.60
  assert apply_cruise_max_limit(2.5, False, get_cruise_max_accel(v_ego, vals, 3)) == pytest.approx(0.60)
  # ECO multiplies the same table by MyEcoModeFactor.
  assert apply_cruise_max_limit(2.5, False, get_cruise_max_accel(v_ego, vals, 2, 0.8)) == pytest.approx(0.48)


def test_no_lead_cap_is_lower_and_tapers_near_set_speed():
  assert get_no_lead_cruise_accel_cap(1.0, 30.0, 0.65) == pytest.approx(0.65)
  assert get_no_lead_cruise_accel_cap(1.0, 15.0, 0.65) == pytest.approx(0.455)
  assert get_no_lead_cruise_accel_cap(1.0, 5.0, 0.65) == pytest.approx(0.26)


def test_no_lead_limit_rate_limits_only_positive_accel_rise():
  limited = apply_no_lead_cruise_accel_limit(
    1.2, False, 1.0, 30.0, 0.65, 0.20, 0.25, 0.01)
  assert limited == pytest.approx(0.2025)
  assert apply_no_lead_cruise_accel_limit(
    -1.5, False, 1.0, 30.0, 0.65, 0.20, 0.25, 0.01) == pytest.approx(-1.5)
  assert apply_no_lead_cruise_accel_limit(
    1.2, True, 1.0, 30.0, 0.65, 0.20, 0.25, 0.01) == pytest.approx(1.2)
