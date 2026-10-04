#!/usr/bin/env python3
import numpy as np

from selfdrive.controls.lib.stop_const_decel import (ConstDecelStop, FALLBACK_DECEL, JERK, START_DECEL,
                                                     STOP_GAP, required_decel)

DT = 0.05


def _approach(ctl, v, d, v_lead=0.0, a_lead=0.0, a_now=0.0):
  return ctl.update(True, v, True, d, v_lead, a_lead, a_now, DT)


def test_waits_until_required_decel_reaches_start():
  ctl = ConstDecelStop()
  v = 50 / 3.6
  far = STOP_GAP + v * v / (2 * START_DECEL) + 5.0
  assert _approach(ctl, v, far) is None
  near = STOP_GAP + v * v / (2 * 1.0)
  assert _approach(ctl, v, near) is not None


def test_ramps_from_current_plan_and_holds_target():
  ctl = ConstDecelStop()
  v = 50 / 3.6
  d = STOP_GAP + v * v / (2 * 1.0)
  decel, target = _approach(ctl, v, d, a_now=-0.5)
  assert abs(target - required_decel(v, d, 0.0)) < 1e-6
  assert abs(decel - (0.5 + JERK * DT)) < 1e-6   # starts from the MPC's -0.5, no step
  for _ in range(40):
    decel, target = _approach(ctl, v, d)
  assert abs(decel - target) < 1e-6


def test_hard_case_keeps_mpc_plan():
  ctl = ConstDecelStop()
  v = 60 / 3.6
  d = STOP_GAP + v * v / (2 * (FALLBACK_DECEL + 0.5))
  assert _approach(ctl, v, d) is None            # never activates
  d_ok = STOP_GAP + v * v / (2 * 1.2)
  assert _approach(ctl, v, d_ok) is not None
  assert _approach(ctl, v, STOP_GAP - 0.5, a_now=-2.0) is None   # cut-in: MPC again
  decel, _ = _approach(ctl, v, d_ok, a_now=-2.0)
  assert abs(decel - (2.0 - JERK * DT)) < 1e-6   # resumes from the MPC's decel


def test_releases_when_lead_pulls_away_or_is_lost():
  ctl = ConstDecelStop()
  v = 40 / 3.6
  d = STOP_GAP + v * v / (2 * 1.0)
  assert _approach(ctl, v, d) is not None
  assert _approach(ctl, v, d, v_lead=v + 1.0) is None
  assert not ctl.active
  assert _approach(ctl, v, d) is not None
  assert ctl.update(True, v, False, d, 0.0, 0.0, 0.0, DT) is None
  assert not ctl.active


def test_fast_steady_lead_is_ignored():
  ctl = ConstDecelStop()
  v = 80 / 3.6
  assert _approach(ctl, v, 30.0, v_lead=50 / 3.6, a_lead=0.0) is None


def test_trajectory_is_constant_to_a_stop():
  t = np.array([0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0])
  v, a, j = ConstDecelStop.trajectory(10.0, 1.0, 1.0, t)
  assert np.allclose(a[:9], -1.0)
  assert v[-1] == 0.0 and a[-1] == 0.0
  assert np.all(np.diff(v) <= 1e-9)
  assert len(j) == len(t) - 1
