#!/usr/bin/env python3
from selfdrive.controls.lib.longitudinal_limits import WEAK_LEAD_HOLD_S, WeakSlowLead

DT = 0.05


def _run(steps, **kw):
  args = dict(enabled=True, v_ego=17.0, prob=0.4, d_vis=85.0, v_vis=6.0, lead_status=False, d_lead=0.0)
  args.update(kw)
  w = WeakSlowLead()
  return [w.update(dt=DT, **args) for _ in range(steps)]


def test_slow_weak_camera_lead_caps_accel_after_hold():
  # 2026-10-22 361 s: 61 km/h, camera 78-90 m at ~22 km/h, prob 0.31-0.47
  out = _run(20)
  first = out.index(True)
  assert abs((first + 1) * DT - WEAK_LEAD_HOLD_S) < 1e-6
  assert all(out[first:])


def test_not_for_faster_far_low_prob_slow_ego_or_disabled():
  assert not any(_run(40, v_vis=13.0))           # only 4 m/s slower than us
  assert not any(_run(40, d_vis=130.0))          # beyond 120 m
  assert not any(_run(40, prob=0.25))
  assert not any(_run(40, v_ego=4.0, v_vis=0.0))
  assert not any(_run(40, enabled=False))


def test_confirmed_lead_at_the_same_place_takes_over():
  assert not any(_run(40, lead_status=True, d_lead=86.0))
  assert all(_run(40, lead_status=True, d_lead=150.0)[20:])   # radar on a farther car


def test_single_frame_dropout_does_not_restart_the_wait():
  w = WeakSlowLead()
  args = dict(enabled=True, v_ego=17.0, d_vis=85.0, v_vis=6.0, lead_status=False, d_lead=0.0, dt=DT)
  seq = [0.4] * 6 + [0.2] + [0.4] * 8
  out = [w.update(prob=p, **args) for p in seq]
  assert out[-1]


def test_brief_dropout_keeps_easing_off_and_confirmed_lead_hands_over():
  w = WeakSlowLead()
  args = dict(enabled=True, v_ego=17.0, d_vis=85.0, v_vis=6.0, lead_status=False, d_lead=0.0, dt=DT)
  for _ in range(12):
    w.update(prob=0.4, **args)
  assert all(w.update(prob=0.1, **args) for _ in range(15))     # 0.75 s camera dropout
  assert not w.update(prob=0.9, **{**args, 'lead_status': True, 'd_lead': 84.0})
  assert w.update(prob=0.1, **args)   # lead flickers back to unconfirmed -> ease off again
