#!/usr/bin/env python3
from selfdrive.controls.lib.longitudinal_limits import (CUT_IN_RELIEF_DECAY, CutInGapRelief,
                                                        pull_away_lead_accel)

DT = 0.05


def _step(r, d_rel, v_rel=0.0, a_lead=0.0, v_ego=13.0, deficit=10.0, enabled=True):
  return r.update(enabled, True, d_rel, v_rel, a_lead, v_ego, deficit, DT)


def test_cut_in_at_similar_speed_lends_the_missing_gap_and_returns_it_slowly():
  r = CutInGapRelief()
  _step(r, 38., deficit=-5.)
  first = _step(r, 13., deficit=9.5)          # 25 m drop = cut-in
  assert first == 9.5
  out = first
  for _ in range(20):                          # 1 s
    out = _step(r, 13., deficit=9.5)
  assert abs(out - (9.5 - 20 * CUT_IN_RELIEF_DECAY * DT)) < 1e-6


def test_relief_arms_when_radar_confirms_within_window():
  # 857 s: camera says closing -2.8 m/s first, radar then shows -0.2 m/s
  r = CutInGapRelief()
  _step(r, 38., v_rel=-1.2, deficit=-7.)
  assert _step(r, 27., v_rel=-2.8, deficit=12.) == 0.0
  for _ in range(25):
    _step(r, 15., v_rel=-2.0, deficit=15.)
  assert _step(r, 13., v_rel=-0.2, a_lead=-0.7, deficit=9.5) == 9.5


def test_no_relief_when_closing_braking_hard_or_too_close():
  for kwargs in ({'v_rel': -1.5}, {'a_lead': -2.5}, {'d_rel': 9.0, 'v_ego': 13.0}):
    r = CutInGapRelief()
    _step(r, 40.)
    args = {'d_rel': 13.0, **kwargs}
    assert _step(r, **args) == 0.0


def test_relief_drops_immediately_when_lead_starts_braking_hard():
  r = CutInGapRelief()
  _step(r, 40.)
  assert _step(r, 13.) > 0.0
  assert _step(r, 13., a_lead=-3.0) == 0.0
  assert _step(r, 13.) < 1e-6  # no re-arm outside a new cut-in, decays from 0


def test_normal_gap_erosion_without_cut_in_gets_no_relief():
  r = CutInGapRelief()
  _step(r, 30., deficit=-1.)
  out = 0.0
  for i in range(80):                          # 4 s, gap closes slowly
    out = _step(r, 30. - 0.05 * i, deficit=1.0)
  assert out == 0.0


def test_disabled_never_relieves():
  r = CutInGapRelief()
  _step(r, 40., enabled=False)
  assert _step(r, 13., enabled=False) == 0.0


def test_pull_away_fades_stale_lead_decel_only_when_gap_is_sufficient():
  assert pull_away_lead_accel(-1.4, 1.1, True) < -0.5
  assert abs(pull_away_lead_accel(-1.4, 1.1, True) - (-1.4 * (1 - 1.1 / 2.0))) < 1e-6
  assert pull_away_lead_accel(-0.9, 2.5, True) == 0.0
  assert pull_away_lead_accel(-1.4, 1.1, False) == -1.4   # gap short: keep full decel
  assert pull_away_lead_accel(-1.4, -0.5, True) == -1.4   # lead slower: keep
  assert pull_away_lead_accel(-3.0, 1.5, True) == -3.0    # hard brake: keep
  assert pull_away_lead_accel(0.5, 1.5, True) == 0.5
