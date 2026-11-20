"""Closed-loop lateral auto-tune for the torque controller (LateralAutoTune).

torqued fits commanded torque against measured lateral acceleration (open loop). This tuner
instead watches how well the controller actually tracks and nudges the values the feedforward
uses until the feedback has nothing left to fix:

latAccelFactor  In steady curves the feedforward (f) alone should hold the car on the path;
                whatever P/I/D add is the feedforward's error. A factor that is right leaves
                f ~= f + p + i + d, so each steady sample implies factor * f / (f + p + i + d).
                Left and right curves are kept apart and averaged, so road crown and a small
                angle offset cancel.
friction        At small lateral accelerations stiction shows as lag: the car follows the
                (delay-compensated) target late. tau = sum(err * rate) / sum(rate^2) with
                err = desired(t - delay) - actual(t), delay being the measured large-signal lag
                (or SteerActuatorDelay + 0.2 s before one is measured), is the extra lag of small
                manoeuvres in seconds. Late -> more friction, early (overshoot) -> less.
steer delay     Lag of actual behind desired lateral acceleration that best correlates in
                lively driving. The controller asks for the plan at SteerActuatorDelay + 0.2 s
                ahead (drive_helpers), so the matching SteerActuatorDelay is that lag - 0.2 s.
                Applied only with LateralAutoTuneDelay, after DELAY_MIN_WINDOWS measurements,
                0.01 s per evaluation, within SAD_BOUNDS.

Every update is small and bounded around the car's own tune, and nothing moves without enough
clean samples, so a bad drive cannot walk the values away.
"""
from collections import deque

import numpy as np

DT = 0.01                    # controlsState, 100 Hz
EVAL_S = 30.0                # one adjustment at most this often
MIN_SPEED = 12.0             # m/s (43 km/h)

# latAccelFactor
STEADY_ACCEL = (0.5, 2.5)    # |desired lateral accel| m/s^2
STEADY_RATE_MAX = 0.3        # |d desired / dt| m/s^3 over STEADY_WINDOW_S
STEADY_WINDOW_S = 0.5
STEADY_ERROR_MAX = 0.4       # |desired - actual| m/s^2
SIDE_SAMPLES = 1500          # per side, 15 s of steady curve
SIDE_MIN = 300
FACTOR_BOUNDS = (0.75, 1.25)  # x the car's tune
FACTOR_GAIN = 0.3            # fraction of the gap closed per evaluation
FACTOR_STEP_MAX = 0.02       # x the car's tune per evaluation

# friction
SMALL_ACCEL_MAX = 0.6
SMALL_RATE_MIN = 0.2         # m/s^3; flat stretches carry no lag information
FRICTION_MIN_SAMPLES = 2000
LAG_DEADBAND = 0.04          # s
FRICTION_STEP = 0.03         # x current per evaluation
FRICTION_BOUNDS = (0.6, 1.5)  # x the car's tune

# delay (report only)
DELAY_WINDOW_S = 10.0
DELAY_SEARCH = (0.0, 0.8)    # s
DELAY_MIN_STD = 0.15         # m/s^2 of desired lateral accel in the window (0.3: no window in 20 min, 2026-11-20)
DELAY_PLAN_LEAD = 0.2        # s the controller adds to SteerActuatorDelay (drive_helpers)
DELAY_MIN_WINDOWS = 10
SAD_BOUNDS = (0.05, 0.6)     # s
SAD_STEP = 0.01              # s per evaluation

HISTORY_S = 1.0


class LateralAutoTune:
  def __init__(self, base_factor, base_friction, factor=None, friction=None, sad=None):
    self.base_factor = float(base_factor)
    self.base_friction = float(base_friction)
    self.factor = float(factor) if factor else self.base_factor
    self.friction = float(friction) if friction else self.base_friction
    self.factor = float(np.clip(self.factor, *(b * self.base_factor for b in FACTOR_BOUNDS)))
    self.friction = float(np.clip(self.friction, *(b * self.base_friction for b in FRICTION_BOUNDS)))
    self.left = deque(maxlen=SIDE_SAMPLES)
    self.right = deque(maxlen=SIDE_SAMPLES)
    self.history = deque(maxlen=int(HISTORY_S / DT))
    self.lag_num = 0.0
    self.lag_den = 0.0
    self.lag_n = 0
    self.delay_desired = deque(maxlen=int(DELAY_WINDOW_S / DT))
    self.delay_actual = deque(maxlen=int(DELAY_WINDOW_S / DT))
    self.delay_estimate = None
    self.delay_windows = 0
    self.sad = None if sad is None else float(np.clip(sad, *SAD_BOUNDS))
    self.last_lag = None
    self.t = 0.0
    self.next_eval = EVAL_S
    self.updates = 0

  def update(self, active, v_ego, steering_pressed, lane_change, saturated, desired, actual,
             f, p, i, d, steer_delay):
    """One controlsState sample. f/p/i/d: torqueState terms, all in the controller's sign."""
    self.t += DT
    usable = active and not steering_pressed and not lane_change and v_ego > MIN_SPEED
    if not usable:
      self.history.clear()
      self.delay_desired.clear()
      self.delay_actual.clear()
    else:
      self.history.append(desired)
      self._factor_sample(saturated, desired, actual, f, p + i + d)
      self._friction_sample(desired, actual, steer_delay)
      self.delay_desired.append(desired)
      self.delay_actual.append(actual)
    if self.t >= self.next_eval:
      self.next_eval = self.t + EVAL_S
      return self._evaluate()
    return False

  def _factor_sample(self, saturated, desired, actual, f, feedback):
    window = int(STEADY_WINDOW_S / DT)
    if saturated or len(self.history) < window:
      return
    if not STEADY_ACCEL[0] <= abs(desired) <= STEADY_ACCEL[1] or abs(desired - actual) > STEADY_ERROR_MAX:
      return
    if abs(self.history[-1] - self.history[-window]) / STEADY_WINDOW_S > STEADY_RATE_MAX:
      return
    total = f + feedback
    if abs(f) < 0.05 or f * total <= 0:
      return
    share = f / total
    if not 0.5 <= share <= 2.0:
      return
    (self.left if desired > 0 else self.right).append(self.factor * share)

  def _friction_sample(self, desired, actual, steer_delay):
    if abs(desired) > SMALL_ACCEL_MAX:
      return
    delay = self.delay_estimate if self.delay_estimate is not None else steer_delay + DELAY_PLAN_LEAD
    lag = int(round(delay / DT))
    rate_n = int(0.2 / DT)
    if len(self.history) <= max(lag, rate_n):
      return
    rate = (self.history[-1] - self.history[-1 - rate_n]) / 0.2
    if abs(rate) < SMALL_RATE_MIN:
      return
    err = self.history[-1 - lag] - actual
    self.lag_num += err * rate
    self.lag_den += rate * rate
    self.lag_n += 1

  def _delay(self):
    if len(self.delay_desired) < self.delay_desired.maxlen:
      return None
    des = np.array(self.delay_desired)
    act = np.array(self.delay_actual)
    if np.std(des) < DELAY_MIN_STD:
      return None
    des = des - des.mean()
    act = act - act.mean()
    best, best_lag = -1.0, None
    for n in range(int(DELAY_SEARCH[0] / DT), int(DELAY_SEARCH[1] / DT) + 1):
      a = des[:len(des) - n] if n else des
      b = act[n:]
      denom = np.linalg.norm(a) * np.linalg.norm(b)
      if denom <= 0:
        continue
      c = float(np.dot(a, b) / denom)
      if c > best:
        best, best_lag = c, n * DT
    return best_lag if best > 0.6 else None

  def _evaluate(self):
    changed = False
    if len(self.left) >= SIDE_MIN and len(self.right) >= SIDE_MIN:
      target = 0.5 * (float(np.median(self.left)) + float(np.median(self.right)))
      lo, hi = (b * self.base_factor for b in FACTOR_BOUNDS)
      target = float(np.clip(target, lo, hi))
      step_max = FACTOR_STEP_MAX * self.base_factor
      step = float(np.clip(FACTOR_GAIN * (target - self.factor), -step_max, step_max))
      if abs(step) > 1e-4:
        self.factor += step
        changed = True
      # keep the newest half: the next estimate should mostly see the new factor
      for side in (self.left, self.right):
        for _ in range(len(side) // 2):
          side.popleft()
    if self.lag_n >= FRICTION_MIN_SAMPLES and self.lag_den > 0:
      self.last_lag = self.lag_num / self.lag_den
      lo, hi = (b * self.base_friction for b in FRICTION_BOUNDS)
      if self.last_lag > LAG_DEADBAND:
        new = min(hi, self.friction * (1 + FRICTION_STEP))
      elif self.last_lag < -LAG_DEADBAND:
        new = max(lo, self.friction * (1 - FRICTION_STEP))
      else:
        new = self.friction
      if abs(new - self.friction) > 1e-6:
        self.friction = new
        changed = True
      self.lag_num = self.lag_den = 0.0
      self.lag_n = 0
    delay = self._delay()
    if delay is not None:
      self.delay_estimate = delay if self.delay_estimate is None else 0.8 * self.delay_estimate + 0.2 * delay
      self.delay_windows += 1
    if self.delay_estimate is not None and self.delay_windows >= DELAY_MIN_WINDOWS:
      target = float(np.clip(self.delay_estimate - DELAY_PLAN_LEAD, *SAD_BOUNDS))
      if self.sad is None:
        self.sad = target
      else:
        self.sad += float(np.clip(target - self.sad, -SAD_STEP, SAD_STEP))
    if changed:
      self.updates += 1
    return changed

  def status(self):
    return {
      "factor": round(self.factor, 4),
      "friction": round(self.friction, 4),
      "base_factor": round(self.base_factor, 4),
      "base_friction": round(self.base_friction, 4),
      "samples_left": len(self.left),
      "samples_right": len(self.right),
      "friction_samples": self.lag_n,
      "last_lag": None if self.last_lag is None else round(self.last_lag, 3),
      "delay": None if self.delay_estimate is None else round(self.delay_estimate, 3),
      "delay_windows": self.delay_windows,
      "sad": None if self.sad is None else round(self.sad, 3),
      "updates": self.updates,
    }
