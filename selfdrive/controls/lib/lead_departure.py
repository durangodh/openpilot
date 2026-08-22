LEAD_DEPARTURE_MIN_SPEED = 0.3
LEAD_DEPARTURE_MIN_DISTANCE_CHANGE = 0.3
LEAD_DEPARTURE_MIN_DISTANCE = 3.0
LEAD_DEPARTURE_MAX_DISTANCE = 40.0
LEAD_DEPARTURE_CONFIRM_TIME = 0.3
LEAD_DEPARTURE_OVERRIDE_TIME = 1.5
STANDSTILL_LATCH_SPEED = 0.05
STANDSTILL_PLAN_RELEASE_TIME = 0.3


class LeadDepartureController:
  """Confirm a real lead departure before bypassing a stale stopped plan."""

  def __init__(self):
    self.reset()

  def reset(self):
    self.base_distance = 0.0
    self.confirm_time = 0.0
    self.override_time = 0.0
    self.departing = False

  def update(self, active, standstill, plan_released, brake_pressed, gas_pressed,
             lead_status, lead_distance, lead_speed, dt):
    if not active or brake_pressed or gas_pressed or not lead_status:
      self.reset()
      return False

    if self.departing:
      self.override_time += dt
      if plan_released or self.override_time >= LEAD_DEPARTURE_OVERRIDE_TIME:
        self.reset()
        return False
      return True

    if not standstill or not (LEAD_DEPARTURE_MIN_DISTANCE <= lead_distance <= LEAD_DEPARTURE_MAX_DISTANCE):
      self.reset()
      return False

    if self.base_distance <= 0.0:
      self.base_distance = lead_distance
    else:
      self.base_distance = min(self.base_distance, lead_distance)

    distance_opened = lead_distance - self.base_distance
    lead_moving = lead_speed >= LEAD_DEPARTURE_MIN_SPEED
    if lead_moving and distance_opened >= LEAD_DEPARTURE_MIN_DISTANCE_CHANGE:
      self.confirm_time += dt
      if self.confirm_time >= LEAD_DEPARTURE_CONFIRM_TIME:
        self.departing = True
        self.override_time = 0.0
        return True
    else:
      self.confirm_time = 0.0

    return False


class StandstillHoldController:
  """Latch a complete stop until departure is confirmed, not merely planned once."""

  def __init__(self):
    self.lead_departure = LeadDepartureController()
    self.reset()

  def reset(self):
    self.latched = False
    self.plan_release_time = 0.0
    self.lead_departure.reset()

  def update(self, active, stopping, v_ego, plan_starting, soft_hold,
             brake_pressed, gas_pressed, resume_pressed,
             lead_status, lead_distance, lead_speed, dt):
    if not active and not soft_hold:
      self.reset()
      return False, False

    if not self.latched and stopping and v_ego < STANDSTILL_LATCH_SPEED:
      self.latched = True
      self.plan_release_time = 0.0
      self.lead_departure.reset()

    if not self.latched:
      return False, False

    lead_release = self.lead_departure.update(
      active=True,
      standstill=True,
      plan_released=False,
      brake_pressed=brake_pressed,
      gas_pressed=False,
      lead_status=lead_status,
      lead_distance=lead_distance,
      lead_speed=lead_speed,
      dt=dt,
    )

    if not lead_status and plan_starting and not brake_pressed:
      self.plan_release_time += dt
    else:
      self.plan_release_time = 0.0

    driver_release = gas_pressed or resume_pressed
    # Driver soft-hold must remain explicit: only gas/RES may release it.
    release = driver_release or (not soft_hold and (
      lead_release or self.plan_release_time >= STANDSTILL_PLAN_RELEASE_TIME))

    if release:
      self.latched = False
      self.plan_release_time = 0.0
      self.lead_departure.reset()
      return False, True

    return True, False
