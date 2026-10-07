"""Extra steering lookahead for a coherent, rapidly tightening low-speed bend."""
import numpy as np


CURVE_ENTRY_EXTRA_DELAY_MAX = 0.30
CURVE_ENTRY_MIN_SPEED = 4.0       # m/s
CURVE_ENTRY_FULL_SPEED = 13.0
CURVE_ENTRY_ZERO_SPEED = 17.0
CURVE_ENTRY_PREVIEW_START = 0.20  # s
CURVE_ENTRY_PREVIEW_END = 1.00    # s


def curve_entry_extra_delay(v_ego, curvatures, t_idxs):
  """Return 0..0.30 s additional lookahead only while a bend tightens.

  A constant bend returns zero. Opposing future curvature (an S bend or model
  wobble) also returns zero, so the controller is not pulled across the lane.
  """
  try:
    c = np.asarray(curvatures, dtype=float)
    t = np.asarray(t_idxs, dtype=float)
  except (TypeError, ValueError):
    return 0.0
  if (c.ndim != 1 or t.shape != c.shape or len(c) < 2 or
      not np.isfinite(c).all() or not np.isfinite(t).all() or
      not np.isfinite(v_ego) or v_ego < CURVE_ENTRY_MIN_SPEED or
      v_ego >= CURVE_ENTRY_ZERO_SPEED):
    return 0.0

  window = (t >= CURVE_ENTRY_PREVIEW_START) & (t <= CURVE_ENTRY_PREVIEW_END)
  future = c[window]
  if future.size == 0:
    return 0.0
  peak = float(future[np.argmax(np.abs(future))])
  if abs(peak) < 1e-6:
    return 0.0

  # Reject a meaningful reversal inside the preview. It is safer to let MPC
  # handle an S bend at its normal delay than to anticipate the first side.
  if np.any(future * peak < -0.25 * peak * peak):
    return 0.0

  current_lat_accel = abs(float(c[0])) * v_ego * v_ego
  peak_lat_accel = abs(peak) * v_ego * v_ego
  tightening = peak_lat_accel - current_lat_accel
  if peak_lat_accel < 1.2 or tightening <= 0.6:
    return 0.0

  demand = float(np.interp(tightening, [0.6, 1.5],
                           [0.0, CURVE_ENTRY_EXTRA_DELAY_MAX]))
  speed_weight = float(np.interp(v_ego,
                                 [CURVE_ENTRY_FULL_SPEED, CURVE_ENTRY_ZERO_SPEED],
                                 [1.0, 0.0]))
  return demand * speed_weight
