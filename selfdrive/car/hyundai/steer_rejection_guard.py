class SteerRejectionGuard:
  """Stop a rejected nonzero steering stream until engagement has ended.

  A rejected frame is evidence of a TX denial, not its reason. Do not retry
  automatically based on MDPS status or relax panda's safety checks.
  """

  def __init__(self):
    self.fault = False

  def update(self, enabled, rejected_torques):
    if not enabled:
      self.fault = False
    elif any(torque != 0 for torque in rejected_torques):
      self.fault = True
    return self.fault
