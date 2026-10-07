from types import SimpleNamespace as NS

import pytest

from selfdrive.controls.lib.lead_following import NEW_LEAD_CONFIRM_S, LeadConfirm

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
