SOFT_HOLD_ENTRY_TIME = 0.7
SOFT_HOLD_MAX_SPEED = 0.1


class SoftHoldController:
  """Latch brake hold at standstill until the driver explicitly departs."""

  def __init__(self):
    self.active = False
    self.released = False
    self.entry_time = 0.0

  def reset(self):
    self.active = False
    self.released = False
    self.entry_time = 0.0

  def update(self, enabled, brake_pressed, gas_pressed, v_ego, resume_pressed, drive_gear, dt):
    self.released = False

    if not enabled or not drive_gear:
      self.reset()
      return False

    if self.active:
      if gas_pressed or resume_pressed:
        self.active = False
        self.released = True
      return self.active

    if brake_pressed and v_ego < SOFT_HOLD_MAX_SPEED:
      self.entry_time += max(0.0, dt)
      if self.entry_time + 1e-9 >= SOFT_HOLD_ENTRY_TIME:
        self.active = True
    else:
      self.entry_time = 0.0

    return self.active
