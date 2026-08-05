LOW_SPEED_LONG_MIN_SPEED = 2.0 / 3.6   # 2 km/h
LOW_SPEED_LONG_MAX_SPEED = 30.0 / 3.6  # stock leadless threshold
LOW_SPEED_LONG_REQUEST_TIME = 1.0


class LowSpeedLongEngage:
  """Temporarily allow SCC commands after an explicit low-speed SET/RES press."""

  def __init__(self):
    self.remaining = 0.0

  def reset(self):
    self.remaining = 0.0

  def update(self, available, request_pressed, brake_pressed, v_ego, has_lead, dt):
    if not available or brake_pressed:
      self.reset()
      return False

    low_speed_request = (LOW_SPEED_LONG_MIN_SPEED <= v_ego < LOW_SPEED_LONG_MAX_SPEED)
    if request_pressed and (has_lead or low_speed_request):
      self.remaining = LOW_SPEED_LONG_REQUEST_TIME
    else:
      self.remaining = max(0.0, self.remaining - dt)

    return self.remaining > 0.0
