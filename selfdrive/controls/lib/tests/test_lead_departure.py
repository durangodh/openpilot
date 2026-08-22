from selfdrive.controls.lib.lead_departure import LEAD_DEPARTURE_CONFIRM_TIME, LEAD_DEPARTURE_OVERRIDE_TIME, \
  STANDSTILL_PLAN_RELEASE_TIME, LeadDepartureController, StandstillHoldController


DT = 0.01


def update(controller, **overrides):
  args = dict(
    active=True,
    standstill=True,
    plan_released=False,
    brake_pressed=False,
    gas_pressed=False,
    lead_status=True,
    lead_distance=7.6,
    lead_speed=0.0,
    dt=DT,
  )
  args.update(overrides)
  return controller.update(**args)


def test_lead_departure_requires_speed_distance_and_confirmation():
  controller = LeadDepartureController()
  assert not update(controller)
  assert not update(controller, lead_distance=8.0, lead_speed=0.2)

  result = False
  for _ in range(int(LEAD_DEPARTURE_CONFIRM_TIME / DT) + 1):
    result = update(controller, lead_distance=8.0, lead_speed=0.4)
  assert result


def test_lead_departure_rejects_single_frame_noise():
  controller = LeadDepartureController()
  assert not update(controller)
  assert not update(controller, lead_distance=8.0, lead_speed=0.4)
  assert not update(controller, lead_distance=8.0, lead_speed=0.0)

  for _ in range(int(LEAD_DEPARTURE_CONFIRM_TIME / DT) - 1):
    assert not update(controller, lead_distance=8.0, lead_speed=0.4)


def test_lead_departure_resets_for_driver_or_invalid_lead():
  controller = LeadDepartureController()
  assert not update(controller)
  for _ in range(int(LEAD_DEPARTURE_CONFIRM_TIME / DT) + 1):
    departing = update(controller, lead_distance=8.0, lead_speed=0.4)
  assert departing
  assert not update(controller, lead_distance=8.0, lead_speed=0.4, brake_pressed=True)
  assert not update(controller, lead_distance=8.0, lead_speed=0.4, lead_status=False)


def test_lead_departure_hands_back_when_plan_releases():
  controller = LeadDepartureController()
  assert not update(controller)
  for _ in range(int(LEAD_DEPARTURE_CONFIRM_TIME / DT) + 1):
    departing = update(controller, lead_distance=8.0, lead_speed=0.4)
  assert departing
  assert not update(controller, standstill=False, plan_released=True,
                    lead_distance=8.2, lead_speed=0.5)


def test_lead_departure_rejects_unsafe_distance():
  for distance in (2.5, 45.0):
    controller = LeadDepartureController()
    for _ in range(int(LEAD_DEPARTURE_CONFIRM_TIME / DT) + 2):
      assert not update(controller, lead_distance=distance, lead_speed=0.5)


def test_lead_departure_override_has_a_time_limit():
  controller = LeadDepartureController()
  assert not update(controller)
  for _ in range(int(LEAD_DEPARTURE_CONFIRM_TIME / DT) + 1):
    departing = update(controller, lead_distance=8.0, lead_speed=0.4)
  assert departing

  result = True
  for _ in range(int(LEAD_DEPARTURE_OVERRIDE_TIME / DT) + 1):
    result = update(controller, standstill=False, lead_distance=8.5, lead_speed=0.5)
  assert not result



def hold_update(controller, **overrides):
  args = dict(
    active=True,
    stopping=True,
    v_ego=0.0,
    plan_starting=False,
    soft_hold=False,
    brake_pressed=False,
    gas_pressed=False,
    resume_pressed=False,
    lead_status=True,
    lead_distance=7.6,
    lead_speed=0.0,
    dt=DT,
  )
  args.update(overrides)
  return controller.update(**args)


def test_standstill_latch_rejects_single_planner_release_frame():
  controller = StandstillHoldController()
  latched, released = hold_update(controller)
  assert latched and not released
  latched, released = hold_update(controller, lead_status=False, plan_starting=True)
  assert latched and not released
  latched, released = hold_update(controller, lead_status=False, plan_starting=False)
  assert latched and not released


def test_no_lead_plan_requires_continuous_confirmation():
  controller = StandstillHoldController()
  hold_update(controller, lead_status=False)
  result = (True, False)
  for _ in range(int(STANDSTILL_PLAN_RELEASE_TIME / DT) + 1):
    result = hold_update(controller, lead_status=False, plan_starting=True)
    if result[1]:
      break
  assert result == (False, True)


def test_lead_departure_releases_latch_after_speed_distance_confirmation():
  controller = StandstillHoldController()
  hold_update(controller)
  result = (True, False)
  for _ in range(int(LEAD_DEPARTURE_CONFIRM_TIME / DT) + 1):
    result = hold_update(controller, lead_distance=8.0, lead_speed=0.4)
    if result[1]:
      break
  assert result == (False, True)


def test_soft_hold_requires_driver_release():
  controller = StandstillHoldController()
  hold_update(controller, soft_hold=True, lead_status=False)
  for _ in range(int(STANDSTILL_PLAN_RELEASE_TIME / DT) + 2):
    latched, released = hold_update(
      controller, soft_hold=True, lead_status=False, plan_starting=True)
    assert latched and not released
  assert hold_update(controller, soft_hold=True, lead_status=False,
                     resume_pressed=True) == (False, True)


def test_latch_resets_when_longitudinal_control_is_canceled():
  controller = StandstillHoldController()
  hold_update(controller)
  assert hold_update(controller, active=False, stopping=False) == (False, False)
  assert not controller.latched
