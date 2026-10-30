from types import SimpleNamespace as NS

from selfdrive.controls.lib.stopping_lead import HeldLead, StoppingLeadFilter

DT = 0.05


def lead(d, v_lead=0.0, v_rel=0.0, a=0.0, status=True):
  return NS(status=status, radar=True, dRel=d, vRel=v_rel, vLead=v_lead, vLeadK=v_lead, aLeadK=a, aLeadTau=1.5)


def run(f, leads, v_ego=0.0):
  return [f.held(l, v_ego, DT) for l in leads]


def test_stable_stopped_lead_is_held_after_0_2_s_and_noise_is_ignored():
  f = StoppingLeadFilter()
  out = run(f, [lead(5.0)] * 4)
  assert out == [False] * 4
  assert f.held(lead(5.0), 0.0, DT)
  # 5 cm range quantization with noisy speed stays held.
  assert f.held(lead(5.05, v_lead=0.25, v_rel=0.25), 0.0, DT)
  assert f.held(lead(5.10, v_lead=0.25, v_rel=0.25), 0.0, DT)


def test_real_departure_releases_after_0_15_m_for_0_1_s():
  f = StoppingLeadFilter()
  run(f, [lead(5.0)] * 6)
  assert f.held(lead(5.0), 0.0, DT)
  assert f.held(lead(5.2, v_lead=0.3, v_rel=0.3), 0.0, DT)
  assert f.held(lead(5.25, v_lead=0.4, v_rel=0.4), 0.0, DT)
  assert not f.held(lead(5.3, v_lead=0.5, v_rel=0.5), 0.0, DT)
  assert not f.held(lead(5.4, v_lead=0.6, v_rel=0.6), 0.0, DT)


def test_moving_and_accelerating_lead_releases_before_the_range_opens():
  f = StoppingLeadFilter()
  run(f, [lead(5.0)] * 6)
  assert f.held(lead(5.0), 0.0, DT)
  # Speed noise without acceleration stays held, even when it lasts.
  assert all(run(f, [lead(5.0, v_lead=0.25, v_rel=0.25)] * 6))
  # Radar motion with acceleration for 0.1 s releases at the same range.
  assert f.held(lead(5.0, v_lead=0.15, v_rel=0.15, a=1.2), 0.0, DT)
  assert f.held(lead(5.0, v_lead=0.17, v_rel=0.17, a=1.2), 0.0, DT)
  assert not f.held(lead(5.0, v_lead=0.20, v_rel=0.20, a=1.3), 0.0, DT)
  # Once departed it stays released for this lead.
  assert not f.held(lead(5.0), 0.0, DT)


def test_a_single_motion_spike_does_not_release():
  f = StoppingLeadFilter()
  run(f, [lead(5.0)] * 6)
  assert f.held(lead(5.0, v_lead=0.2, v_rel=0.2, a=1.0), 0.0, DT)
  assert f.held(lead(5.0), 0.0, DT)
  assert f.held(lead(5.0, v_lead=0.2, v_rel=0.2, a=1.0), 0.0, DT)
  assert f.held(lead(5.0), 0.0, DT)


def test_closing_range_moving_ego_and_decelerating_lead_are_not_held():
  f = StoppingLeadFilter()
  run(f, [lead(5.0)] * 6)
  assert f.held(lead(5.0), 0.0, DT)
  assert not f.held(lead(4.8), 0.0, DT)
  f = StoppingLeadFilter()
  assert not any(run(f, [lead(5.0)] * 10, v_ego=1.0))
  f = StoppingLeadFilter()
  run(f, [lead(5.0)] * 6)
  assert not f.held(lead(5.0, a=-1.0), 0.0, DT)


def test_range_jump_is_a_new_lead_and_lost_lead_resets():
  f = StoppingLeadFilter()
  run(f, [lead(5.0)] * 6)
  assert f.held(lead(5.0), 0.0, DT)
  assert not f.held(lead(8.0), 0.0, DT)
  assert not f.held(lead(8.0, status=False), 0.0, DT)


def test_held_view_zeroes_only_planner_motion():
  held = HeldLead(lead(5.0, v_lead=0.2, v_rel=0.2, a=0.3))
  assert (held.vLead, held.vLeadK, held.aLeadK) == (0.0, 0.0, 0.0)
  assert held.dRel == 5.0 and held.vRel == 0.2 and held.status
