"""설정속도 하강 타행(A)과 가속 중 jerk 비용 보정(C) 테스트."""
from types import SimpleNamespace as NS

import pytest

from selfdrive.controls.lib.lead_following import ACCEL_JERK_COST_SCALE, get_accel_jerk_scale
from selfdrive.controls.lib.longitudinal_limits import (CRUISE_COAST_FLOOR,
                                                        get_cruise_coast_min_accel,
                                                        lead_blocks_cruise_coast)

A_MIN = -1.2


def lead(status=True, vLead=25.0, aLeadK=0.0, dRel=80.0):
  return NS(status=status, vLead=vLead, aLeadK=aLeadK, dRel=dRel)


def test_small_overspeed_coasts_and_large_overspeed_brakes_fully():
  v = 100 / 3.6
  assert get_cruise_coast_min_accel(v, v - 0.3, A_MIN) == pytest.approx(CRUISE_COAST_FLOOR)
  mid = get_cruise_coast_min_accel(v, v - 1.5, A_MIN)
  assert A_MIN < mid < CRUISE_COAST_FLOOR
  assert get_cruise_coast_min_accel(v, v - 4.0, A_MIN) == pytest.approx(A_MIN)


def test_floor_is_monotonic_in_overspeed():
  v = 80 / 3.6
  floors = [get_cruise_coast_min_accel(v, v - e / 10.0, A_MIN) for e in range(0, 40)]
  assert all(a >= b - 1e-9 for a, b in zip(floors, floors[1:]))


def test_blocked_or_low_speed_keeps_full_floor():
  assert get_cruise_coast_min_accel(20.0, 19.9, A_MIN, blocked=True) == A_MIN
  assert get_cruise_coast_min_accel(3.0, 2.9, A_MIN) == A_MIN


def test_braking_relevant_leads_block_coast():
  v = 25.0
  assert not lead_blocks_cruise_coast(lead(status=False), v)
  assert not lead_blocks_cruise_coast(lead(vLead=26.0, dRel=80.0), v)
  assert lead_blocks_cruise_coast(lead(vLead=24.0), v)            # 접근 중
  assert lead_blocks_cruise_coast(lead(vLead=26.0, aLeadK=-0.5), v)  # 앞차 감속
  assert lead_blocks_cruise_coast(lead(vLead=26.0, dRel=30.0), v)    # 가까움


def test_jerk_scale_only_while_accelerating_without_closing_lead():
  v = 70 / 3.6
  assert get_accel_jerk_scale(v, 0.5, 0.5, (lead(False),)) == pytest.approx(ACCEL_JERK_COST_SCALE)
  assert get_accel_jerk_scale(v, -0.1, 0.5, (lead(False),)) == 1.0          # 감속 중
  assert get_accel_jerk_scale(v, 0.5, 0.5, (lead(vLead=v - 1.0),)) == 1.0   # 접근 중
  assert get_accel_jerk_scale(v, 0.5, 0.5, (lead(vLead=v + 1, aLeadK=-0.5),)) == 1.0
  assert get_accel_jerk_scale(20 / 3.6, 0.5, 0.5, (lead(False),)) == 1.0    # 저속 출발은 기존 동적비용
