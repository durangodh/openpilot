"""Auto lane centring (LateralAutoCenter): learns PathOffset from where the car really drives.

On straight, well-marked roads the camera sees both lane lines; their midpoint 8 m ahead
(camera offset already applied) is where the lane centre is relative to the car. A car that
keeps sitting right of centre sees a positive midpoint, so the path is moved left by a small
part of the average every evaluation, until the car sits in the middle.
"""
import numpy as np

DT = 0.05                     # lateralPlan, 20 Hz
EVAL_S = 30.0
MIN_SAMPLES = 400             # 20 s of clean straight driving per evaluation
MIN_SPEED = 15.0              # m/s
LOOK_X = 8.0                  # m ahead
MIN_PROB = 0.7
WIDTH = (2.8, 4.2)            # m
MAX_CURVATURE = 0.0005        # 1/m, radius > 2 km
GAIN = 0.3
STEP_MAX = 0.01               # m per evaluation
BOUNDS = (-0.25, 0.25)        # m


class LaneAutoCenter:
  def __init__(self, offset=0.0):
    self.offset = float(np.clip(offset, *BOUNDS))
    self.sum = 0.0
    self.n = 0
    self.t = 0.0
    self.next_eval = EVAL_S
    self.last_mean = None

  def update(self, usable, v_ego, curvature, ll_x, lll_y, rll_y, lll_prob, rll_prob):
    """Returns True when the offset changed."""
    self.t += DT
    if usable and v_ego > MIN_SPEED and abs(curvature) < MAX_CURVATURE and \
       lll_prob > MIN_PROB and rll_prob > MIN_PROB and len(ll_x) > 1:
      left = float(np.interp(LOOK_X, ll_x, lll_y))
      right = float(np.interp(LOOK_X, ll_x, rll_y))
      if WIDTH[0] <= left - right <= WIDTH[1]:
        self.sum += 0.5 * (left + right)
        self.n += 1
    if self.t < self.next_eval:
      return False
    self.next_eval = self.t + EVAL_S
    if self.n < MIN_SAMPLES:
      return False
    self.last_mean = self.sum / self.n
    self.sum = 0.0
    self.n = 0
    step = float(np.clip(GAIN * self.last_mean, -STEP_MAX, STEP_MAX))
    new = float(np.clip(self.offset + step, *BOUNDS))
    changed = abs(new - self.offset) > 1e-5
    self.offset = new
    return changed
