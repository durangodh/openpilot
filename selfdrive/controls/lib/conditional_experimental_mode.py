"""Local, opt-in ACC/E2E selection. No parameter writes or extra subscribers."""
import math


# Existing longitudinalPlan.e2eReason values 0..5 retain their meanings.
CURVE, LEAD, LOW_SPEED, NAVIGATION = 6, 7, 8, 9


def select_longitudinal_mode(base_mode, manual_experimental, conditional_reason, preserve_stop):
  """Keep the aPilot result independent of temporary E2E overrides."""
  if manual_experimental:
    return 'blended', False
  conditional = bool(conditional_reason and base_mode == 'acc' and not preserve_stop)
  return ('blended' if conditional else base_mode), conditional


class ConditionalExperimentalMode:
  ENTER_CONFIRM_S = 0.5
  CLEAR_CONFIRM_S = 1.0
  MIN_ACTIVE_S = 5.0
  MAX_FRAME_GAP_S = 0.5

  def __init__(self):
    self.reset()

  def reset(self):
    self.reason = 0
    self.candidate = 0
    self.candidate_since = None
    self.clear_since = None
    self.active_since = None
    self.last_time = None

  @staticmethod
  def navigation_matches(navigation):
    if (not isinstance(navigation, dict) or not navigation.get('fresh') or
        navigation.get('off_route') or
        navigation.get('kind') not in ('turn', 'uturn', 'fork', 'rotary')):
      return False
    try:
      distance = float(navigation.get('distance'))
      return math.isfinite(distance) and 0.0 <= distance <= 200.0
    except (TypeError, ValueError, OverflowError):
      return False

  @staticmethod
  def condition(carstate, radarstate, model, navigation):
    """None means unusable control inputs; zero means no condition matched."""
    try:
      speed = float(carstate.vEgo)
      yaw = list(model.orientationRate.z)
      position = list(model.position.x)
      velocity = list(model.velocity.x)
      if (speed < 0 or not math.isfinite(speed) or
          any(len(values) != 33 or not all(math.isfinite(v) for v in values)
              for values in (yaw, position, velocity))):
        return None
      leads = []
      for lead in (radarstate.leadOne, radarstate.leadTwo):
        if lead.status:
          distance, lead_speed = float(lead.dRel), float(lead.vLead)
          if not math.isfinite(distance) or not math.isfinite(lead_speed) or distance <= 0:
            return None
          leads.append((distance, max(0.0, lead_speed)))
    except (AttributeError, TypeError, ValueError, OverflowError):
      return None

    speed_kph = speed * 3.6
    # Real model orientationRate, not the missing phone-side curvature object.
    # The first 17 model points cover the near part of the predicted turn.
    if not leads and speed_kph <= 40.0 and max(abs(v) for v in yaw[:17]) >= 0.05:
      return CURVE
    if any(distance <= 80.0 and (speed - lead_speed > 7.0 / 3.6 or lead_speed < 1.0)
           for distance, lead_speed in leads):
      return LEAD
    if speed_kph < (20.0 if leads else 30.0):
      return LOW_SPEED
    if ConditionalExperimentalMode.navigation_matches(navigation):
      return NAVIGATION
    return 0

  def update(self, now, enabled, available, carstate, radarstate, model, navigation=None):
    # Disable, stale messages, pedal intervention and loss of longitudinal
    # authority bypass every debounce/minimum-time gate.
    if not enabled or not available or not math.isfinite(now):
      self.reset()
      return 0
    if self.last_time is not None and not 0.0 <= now - self.last_time <= self.MAX_FRAME_GAP_S:
      self.reset()
    self.last_time = now
    reason = self.condition(carstate, radarstate, model, navigation)
    if reason is None:
      self.reset()
      return 0
    # A navigation-only activation must never survive lost/ended guidance.
    nav_valid = self.navigation_matches(navigation)
    if self.reason == NAVIGATION and not nav_valid:
      self.reset()
      self.last_time = now
    if reason:
      self.clear_since = None
      if self.reason:
        self.reason = reason
      elif self.candidate != reason:
        self.candidate, self.candidate_since = reason, now
      elif now - self.candidate_since >= self.ENTER_CONFIRM_S:
        self.reason, self.active_since = reason, now
    else:
      self.candidate, self.candidate_since = 0, None
      if self.reason:
        if self.clear_since is None:
          self.clear_since = now
        if now - self.active_since >= self.MIN_ACTIVE_S and now - self.clear_since >= self.CLEAR_CONFIRM_S:
          self.reset()
          self.last_time = now
    return self.reason
