import math

from selfdrive.car.hyundai.scc_lead_tracker import (
  SCC_DROPOUT_HOLD_FRAMES,
  SCC_MAX_REL_ACCEL,
  SCC_MIN_REL_ACCEL,
  SCCLeadTracker,
  scc_object_valid,
)


def test_scc_validity_uses_both_flags_and_rejects_empty_or_nonfinite_values():
  assert scc_object_valid(1, 1, 40.0, 0.2, -2.0)
  assert not scc_object_valid(0, 1, 40.0, 0.2, -2.0)
  assert not scc_object_valid(1, 0, 40.0, 0.2, -2.0)
  assert not scc_object_valid(1, 1, 204.7, 0.2, -2.0)
  assert not scc_object_valid(1, 1, float('nan'), 0.2, -2.0)


def test_continuous_target_keeps_id_and_produces_bounded_acceleration():
  tracker = SCCLeadTracker()
  first = tracker.update(1, 1, 40.0, 0.2, -1.0)
  second = tracker.update(1, 1, 39.96, 0.2, -1.5)

  assert first.track_id == second.track_id == 0
  assert first.measured and second.measured
  assert math.isnan(first.a_rel)
  assert SCC_MIN_REL_ACCEL <= second.a_rel <= SCC_MAX_REL_ACCEL
  assert second.a_rel < 0.0


def test_physical_target_handoff_resets_filter_and_assigns_new_id():
  tracker = SCCLeadTracker()
  first = tracker.update(1, 1, 70.0, 0.1, -1.0)
  tracker.update(1, 1, 69.95, 0.1, -1.2)
  replacement = tracker.update(1, 1, 35.0, -0.4, -12.0)

  assert first.track_id == 0
  assert replacement.track_id == 1
  assert replacement.d_rel == 35.0
  assert replacement.v_rel == -12.0
  assert math.isnan(replacement.a_rel)


def test_same_range_velocity_handoff_still_assigns_new_id():
  tracker = SCCLeadTracker()
  first = tracker.update(1, 1, 40.0, 0.0, -1.0)
  replacement = tracker.update(1, 1, 39.98, 0.0, -10.0)

  assert replacement.track_id == first.track_id + 1
  assert replacement.d_rel == 39.98
  assert replacement.v_rel == -10.0


def test_short_dropout_is_unmeasured_and_expires_without_track_id_reuse():
  tracker = SCCLeadTracker()
  first = tracker.update(1, 1, 25.0, 0.0, -2.0)

  held = []
  for _ in range(SCC_DROPOUT_HOLD_FRAMES):
    held.append(tracker.update(0, 0, 204.7, 0.0, 0.0))

  assert all(sample is not None and not sample.measured for sample in held)
  assert all(sample.track_id == first.track_id for sample in held)
  assert tracker.update(0, 0, 204.7, 0.0, 0.0) is None

  reacquired = tracker.update(1, 1, 24.8, 0.0, -2.0)
  assert reacquired.track_id == first.track_id + 1


def test_compatible_reacquisition_inside_hold_keeps_identity():
  tracker = SCCLeadTracker()
  first = tracker.update(1, 1, 30.0, 0.0, -1.0)
  held = tracker.update(0, 0, 204.7, 0.0, 0.0)
  reacquired = tracker.update(1, 1, held.d_rel - 0.02, 0.0, -1.0)

  assert not held.measured
  assert reacquired.measured
  assert reacquired.track_id == first.track_id


def test_distance_quantization_does_not_create_false_acceleration():
  tracker = SCCLeadTracker()
  samples = [tracker.update(1, 1, 40.0 + (index % 2) * 0.1, 0.0, 0.0)
             for index in range(12)]

  assert all(abs(sample.v_rel) < 1e-9 for sample in samples)
  assert all(math.isnan(sample.a_rel) or abs(sample.a_rel) < 1e-9 for sample in samples)


def test_smooth_braking_motion_does_not_churn_track_identity():
  tracker = SCCLeadTracker()
  d_rel = 80.0
  v_rel = 0.0
  track_ids = []
  samples = []
  for _ in range(100):
    v_rel = max(-4.0, v_rel - 0.04)
    d_rel += v_rel * tracker.dt
    sample = tracker.update(1, 1, d_rel, 0.1, v_rel)
    track_ids.append(sample.track_id)
    samples.append(sample)

  assert set(track_ids) == {0}
  assert samples[-1].a_rel < -1.0
  assert SCC_MIN_REL_ACCEL <= samples[-1].a_rel <= SCC_MAX_REL_ACCEL
