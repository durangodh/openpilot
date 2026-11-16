"""앞차 출발 알림 (lead departed while ego still stands).

Ego stopped (< 0.3 m/s) for 1 s with the radar lead within 20 m: when that lead moves
2.5 m further away while ego still stands, fire once for this stop. Used for the steering
wheel haptic (LKAS11 lane warning bits, like HapticFeedbackWhenSpeedCamera); the stock HUD
text (mcan_hud) keeps its own copy of the same rule.
"""

STOPPED_FRAMES = 100       # 1 s at 100 Hz
LEAD_MAX_M = 20.0
GAP_M = 2.5


class LeadDepartDetector:
  def __init__(self):
    self.stopped_frames = 0
    self.stop_lead_d = None
    self.fired = False

  def update(self, v_ego, lead_d, in_drive=True):
    """True on the one frame the lead is seen departing. Only in D: out of D (P, N, R) the stop
    starts over, so a parked or neutral car never vibrates."""
    if not in_drive:
      self.stopped_frames = 0
      self.stop_lead_d = None
      self.fired = False
      return False
    if v_ego < 0.3:
      self.stopped_frames += 1
    elif v_ego > 1.0:
      self.stopped_frames = 0
    if self.stopped_frames < STOPPED_FRAMES:
      self.stop_lead_d = None
      self.fired = False
      return False
    if lead_d is None or self.fired:
      return False
    if self.stop_lead_d is None:
      if lead_d <= LEAD_MAX_M:
        self.stop_lead_d = lead_d
      return False
    if lead_d - self.stop_lead_d >= GAP_M:
      self.fired = True
      return True
    return False
