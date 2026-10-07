"""Validate lane samples before they influence a model path."""
import numpy as np


def valid_samples(*arrays, size=33):
  try:
    return all(np.asarray(a).shape == (size,) and np.isfinite(a).all() for a in arrays)
  except (TypeError, ValueError):
    return False


def valid_lane_path(t, x, left, right):
  return (valid_samples(x, left, right) and valid_lane_times(t) and
          bool(np.all(np.diff(x) > 0.0)))


def valid_lane_times(t):
  # C2 modeld emits a finite prefix ending at 10s and NaN padding when the
  # position plan does not reach all 192m lane samples. This is NORMAL at low
  # speeds. Reject holes/reversed times/Inf, not this documented trailing pad.
  try:
    t = np.asarray(t, dtype=float)
    if t.shape != (33,) or np.isinf(t).any():
      return False
    count = int(np.isfinite(t).sum())
    return (count >= 2 and bool(np.isfinite(t[:count]).all()) and
            bool(np.isnan(t[count:]).all()) and t[0] >= 0.0 and
            bool(np.all(np.diff(t[:count]) > 0.0)))
  except (TypeError, ValueError):
    return False


def covers_lane_horizon(t, query_t):
  """C2's N=32 lateral MPC consumes all 33 points, including the terminal."""
  if not valid_lane_times(t) or not valid_samples(query_t) or not np.all(np.diff(query_t) > 0.0):
    return False
  finite_t = np.asarray(t)[np.isfinite(t)]
  return finite_t[0] <= query_t[0] + 1e-6 and finite_t[-1] + 1e-6 >= query_t[-1]


def lane_horizon_weights(t, query_t):
  """Use measured lanes nearby; smoothly return to the model beyond coverage."""
  if covers_lane_horizon(t, query_t):
    return np.ones(len(query_t))
  finite_t = np.asarray(t)[np.isfinite(t)]
  end = float(finite_t[-1])
  start = max(float(query_t[0]), end - 1.0)
  if end <= start:
    return np.zeros(len(query_t))
  weights = np.interp(query_t, [start, end], [1.0, 0.0])
  weights[np.asarray(query_t) < finite_t[0]] = 0.0
  return weights


def curve_centering_weight(curve_speed):
  """Signed curve speed is derived from measured curvature, in km/h.

  Retain input lead near straight travel; fade it away as curvature grows.
  The weight has no timer, so the same geometry works on short and long bends.
  """
  return float(np.interp(abs(curve_speed), [150.0, 200.0], [1.0, 0.0]))


def curve_lane_center_blend(base_blend, curve_speed, lane_prob, lane_std, lane_width_max):
  width_confidence = np.interp(lane_width_max, [4.5, 6.0], [1.0, 0.0])
  std_confidence = np.interp(lane_std, [.15, .3], [1.0, 0.0])
  confidence = np.interp(lane_prob * width_confidence * std_confidence,
                         [0.5, 0.7], [0.0, 1.0])
  weight = curve_centering_weight(curve_speed) * confidence
  return float(base_blend + weight * (1.0 - base_blend))
