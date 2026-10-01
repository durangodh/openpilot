from types import SimpleNamespace as NS

import pytest

from selfdrive.controls.lib.lead_following import (NEW_LEAD_CONFIRM_S, LeadConfirm,
                                                   faster_lead_relief)

DT = 0.05


def lead(d, v):
  return NS(status=True, dRel=d, vLead=v, aLeadK=0.0)


def frames_until_used(confirm, make_lead, v_ego, limit=40):
  for i in range(limit):
    if confirm.update(make_lead(i), v_ego, DT):
      return i
  return None


def test_far_non_closing_new_lead_waits_briefly():
  # 70 km/h, a car appears 60 m ahead at the same speed (e.g. next lane on a curve).
  v = 70 / 3.6
  frames = frames_until_used(LeadConfirm(), lambda i: lead(60.0, v), v)
  assert frames == round(NEW_LEAD_CONFIRM_S / DT) - 1


@pytest.mark.parametrize('d,v_lead', [
  (60.0, 0.0),        # stopped car 60 m ahead at 70 km/h: TTC ~3 s
  (20.0, 70 / 3.6),   # close cut-in at equal speed
  (25.0, 25 / 3.6),   # slower cut-in
])
def test_close_or_closing_lead_is_used_immediately(d, v_lead):
  assert LeadConfirm().update(lead(d, v_lead), 70 / 3.6, DT)


def test_lead_switch_restarts_confirmation_and_loss_resets():
  v = 70 / 3.6
  confirm = LeadConfirm()
  for _ in range(10):
    confirm.update(lead(80.0, v), v, DT)
  assert confirm.update(lead(80.0, v), v, DT)
  # A different, farther vehicle is not used until confirmed.
  assert not confirm.update(lead(110.0, v), v, DT)
  assert not confirm.update(NS(status=False), v, DT)
  assert not confirm.update(lead(80.0, v), v, DT)


def test_faster_cut_in_removes_only_the_deficit():
  v = 70 / 3.6
  # 80 km/h car cuts in 15 m ahead, desired gap 35 m, obstacle equivalent 25 m.
  assert faster_lead_relief(15.0, v, 80 / 3.6, 0.0, 35.0, 25.0, 6.0) == pytest.approx(10.0)
  # Only a few km/h faster: relief fades in with the speed difference.
  assert 0.0 < faster_lead_relief(15.0, v, 75 / 3.6, 0.0, 35.0, 25.0, 6.0) < 10.0


@pytest.mark.parametrize('kwargs', [
  dict(v_lead=70 / 3.6),          # same speed: normal response
  dict(a_lead=-1.0),              # cut-in is braking
  dict(d_rel=6.0),                # too close for any relief
  dict(obstacle_now=40.0),        # no deficit
])
def test_no_relief_for_slow_braking_close_or_spare_gap(kwargs):
  values = dict(d_rel=15.0, v_ego=70 / 3.6, v_lead=75 / 3.6, a_lead=0.0,
                desired_gap=35.0, obstacle_now=25.0, stop_distance=6.0)
  values.update(kwargs)
  assert faster_lead_relief(**values) == 0.0
