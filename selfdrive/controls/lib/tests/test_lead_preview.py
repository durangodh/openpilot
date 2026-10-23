from types import SimpleNamespace as NS

from selfdrive.controls.lib.lead_preview import (LeadDecelPreview, action_time, apply_preview_target,
                                                 lead_accel_signal)


def lead(a, status=True):
  return NS(status=status, aLeadK=a)


def test_signal_deadband_and_ego_accel_reference():
  assert lead_accel_signal(-0.05, 0.0) == 0.0
  assert abs(lead_accel_signal(-1.0, 0.0) + 0.9) < 1e-9
  assert lead_accel_signal(-1.0, -1.0) == 0.0


def test_preview_attacks_fast_releases_slow_and_is_capped():
  p = LeadDecelPreview()
  for _ in range(100):
    p.update(lead(-3.0), 0.0, 0.01)
  assert p.preview_s == 1.5
  p.update(lead(0.0), 0.0, 0.01)
  assert abs(p.preview_s - (1.5 - 0.006)) < 1e-9
  p.reset()
  assert p.update(None, 0.0, 0.01) == 0.0


def test_target_can_only_be_lowered_within_bounds():
  assert apply_preview_target(0.5, -1.0) == -0.03
  assert apply_preview_target(0.5, 0.8) == 0.5
  assert abs(apply_preview_target(-1.0, -2.0) + 1.08) < 1e-9
  assert apply_preview_target(-1.0, -1.05) == -1.05


def test_action_time_bounds():
  assert action_time(0.5, 1.5) == 2.0
  assert action_time(2.0, 1.5) == 2.5
