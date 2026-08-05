from selfdrive.controls.lib.low_speed_long import LOW_SPEED_LONG_REQUEST_TIME, LowSpeedLongEngage


DT_CTRL = 0.01


def test_driver_set_allows_leadless_low_speed_request():
  engage = LowSpeedLongEngage()
  assert engage.update(True, True, False, 10.0 / 3.6, False, DT_CTRL)


def test_low_speed_without_button_does_not_request():
  engage = LowSpeedLongEngage()
  assert not engage.update(True, False, False, 10.0 / 3.6, False, DT_CTRL)


def test_request_stays_active_long_enough_for_scc_commands():
  engage = LowSpeedLongEngage()
  engage.update(True, True, False, 10.0 / 3.6, False, DT_CTRL)
  for _ in range(int(LOW_SPEED_LONG_REQUEST_TIME / DT_CTRL) - 1):
    assert engage.update(True, False, False, 10.0 / 3.6, False, DT_CTRL)
  assert not engage.update(True, False, False, 10.0 / 3.6, False, DT_CTRL)


def test_leadless_standstill_request_is_blocked():
  engage = LowSpeedLongEngage()
  assert not engage.update(True, True, False, 0.0, False, DT_CTRL)


def test_leadless_creep_below_two_kph_is_blocked():
  engage = LowSpeedLongEngage()
  assert not engage.update(True, True, False, 1.0 / 3.6, False, DT_CTRL)


def test_lead_allows_standstill_request():
  engage = LowSpeedLongEngage()
  assert engage.update(True, True, False, 0.0, True, DT_CTRL)


def test_brake_cancels_pending_request():
  engage = LowSpeedLongEngage()
  assert engage.update(True, True, False, 10.0 / 3.6, False, DT_CTRL)
  assert not engage.update(True, False, True, 10.0 / 3.6, False, DT_CTRL)


def test_main_or_long_control_off_cancels_request():
  engage = LowSpeedLongEngage()
  assert engage.update(True, True, False, 10.0 / 3.6, False, DT_CTRL)
  assert not engage.update(False, False, False, 10.0 / 3.6, False, DT_CTRL)


def test_high_speed_path_is_left_to_stock_scc():
  engage = LowSpeedLongEngage()
  assert not engage.update(True, True, False, 35.0 / 3.6, False, DT_CTRL)
