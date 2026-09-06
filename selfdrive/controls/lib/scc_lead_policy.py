#!/usr/bin/env python3
import math


SCC_MISMATCH_MIN_DISTANCE = 0.75
SCC_MISMATCH_MAX_DISTANCE = 160.0
SCC_MISMATCH_MAX_ABS_YREL = 2.5
SCC_MISMATCH_MAX_FARTHER_THAN_VISION = 3.0
SCC_MISMATCH_MIN_ABS_LEAD_SPEED = -2.0

SCC_ACCEL_BLEND_WEIGHT = 0.25
SCC_MIN_LEAD_ACCEL = -6.0
SCC_MAX_LEAD_ACCEL = 4.0


def should_preserve_scc_on_mismatch(d_rel, y_rel, v_rel, v_ego,
                                    vision_d_rel, model_prob, measured):
  """Keep a plausible measured SCC lead when camera association briefly fails.

  A farther SCC target may not replace a nearer visual target beyond a small
  tolerance. Unmeasured dropout predictions are eligible only for ordinary
  vision matching, never for this mismatch override.
  """
  values = (d_rel, y_rel, v_rel, v_ego, vision_d_rel, model_prob)
  if not measured or not all(math.isfinite(float(value)) for value in values):
    return False

  return (float(model_prob) > 0.5 and
          SCC_MISMATCH_MIN_DISTANCE < float(d_rel) < SCC_MISMATCH_MAX_DISTANCE and
          abs(float(y_rel)) <= SCC_MISMATCH_MAX_ABS_YREL and
          float(d_rel) <= float(vision_d_rel) + SCC_MISMATCH_MAX_FARTHER_THAN_VISION and
          float(v_ego) + float(v_rel) >= SCC_MISMATCH_MIN_ABS_LEAD_SPEED)


def blend_scc_lead_accel(kalman_accel, sensor_accel):
  """Blend a bounded SCC-derived lead acceleration into RadarD's estimate."""
  if not math.isfinite(float(sensor_accel)):
    return float(kalman_accel)

  bounded_sensor = max(SCC_MIN_LEAD_ACCEL,
                       min(SCC_MAX_LEAD_ACCEL, float(sensor_accel)))
  return ((1.0 - SCC_ACCEL_BLEND_WEIGHT) * float(kalman_accel) +
          SCC_ACCEL_BLEND_WEIGHT * bounded_sensor)


def clusters_for_lead_two(clusters, scc_only):
  """A single OEM-selected SCC target belongs only to leadOne.

  The model's second hypothesis remains available as a vision-only leadTwo;
  this prevents the same physical SCC point from being published twice.
  """
  return [] if scc_only else clusters
