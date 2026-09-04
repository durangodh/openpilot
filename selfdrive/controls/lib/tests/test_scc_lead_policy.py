import math

from selfdrive.controls.lib.scc_lead_policy import (
  blend_scc_lead_accel,
  clusters_for_lead_two,
  should_preserve_scc_on_mismatch,
)


def preserve(**overrides):
  values = {
    'd_rel': 40.0,
    'y_rel': 0.2,
    'v_rel': -2.0,
    'v_ego': 20.0,
    'vision_d_rel': 45.0,
    'model_prob': 0.9,
    'measured': True,
  }
  values.update(overrides)
  return should_preserve_scc_on_mismatch(**values)


def test_plausible_measured_scc_is_preserved_on_camera_mismatch():
  assert preserve()


def test_dropout_prediction_never_forces_mismatch_override():
  assert not preserve(measured=False)


def test_farther_scc_does_not_hide_a_nearer_visual_lead():
  assert not preserve(d_rel=60.0, vision_d_rel=40.0)
  assert preserve(d_rel=47.9, vision_d_rel=40.0)


def test_implausible_lateral_or_backward_scc_is_rejected():
  assert not preserve(y_rel=3.0)
  assert not preserve(v_rel=-23.0, v_ego=20.0)


def test_scc_acceleration_blend_is_bounded_and_ignores_nan():
  assert blend_scc_lead_accel(-1.0, float('nan')) == -1.0
  assert math.isclose(blend_scc_lead_accel(0.0, -20.0), -1.5)
  assert math.isclose(blend_scc_lead_accel(0.0, 20.0), 1.0)


def test_scc_only_lead_two_is_vision_only():
  clusters = [object()]
  assert clusters_for_lead_two(clusters, True) == []
  assert clusters_for_lead_two(clusters, False) is clusters
