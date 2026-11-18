from selfdrive.eon_cluster.bcan_blinker_core import NooHoldPlanner


def test_noo_hold_starts_once_and_keeps_the_existing_hold():
  p = NooHoldPlanner()
  assert p.update("L", None, True) == "HL"
  for _ in range(1200):  # a long red-light wait does not send a new hold/tap
    assert p.update("L", "L", True, lamps_on=True) is None
  assert p.update(None, "L", True) == "C"
  assert p.update(None, None, True) is None


def test_remote_press_takes_priority_and_is_not_automatically_restarted():
  p = NooHoldPlanner()
  assert p.update("L", None, True) == "HL"
  assert p.update("L", "L", True, remote_request="R") is None
  assert p.update("L", None, True) is None
  assert p.update(None, None, True) is None
  assert p.update("R", None, True) == "HR"


def test_a_driver_or_remote_hold_is_never_taken_over_or_canceled():
  p = NooHoldPlanner()
  assert p.update("L", "R", True) is None
  assert p.update(None, "R", False) is None
  assert p.update("L", None, True, lamps_on=True) is None


def test_disabled_output_or_lost_link_cancels_only_our_hold_once():
  p = NooHoldPlanner()
  assert p.update("R", None, False) is None
  assert p.update("R", None, True) == "HR"
  assert p.update("R", "R", False) == "C"
  assert p.update("R", None, True) is None


def test_rejected_expired_or_manually_ended_hold_does_not_retry_the_same_request():
  p = NooHoldPlanner()
  assert p.update("L", None, True) == "HL"
  assert p.update("L", None, True) is None
  assert p.update("L", None, True) is None
  assert p.update(None, None, True) is None
  assert p.update("L", None, True) == "HL"


def test_opposite_nav_direction_releases_before_starting_another_hold():
  p = NooHoldPlanner()
  assert p.update("L", None, True) == "HL"
  assert p.update("R", "L", True) == "C"
  assert p.update("R", None, True) is None
