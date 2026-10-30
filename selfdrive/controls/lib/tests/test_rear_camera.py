"""RearCameraHold: keep a rear camera's limit for a distance after passing it."""
from selfdrive.controls.lib.rear_camera import (REAR_CAMERA_HOLD_DIST, REAR_CAMERA_LOST_ARM_DIST,
                                                RearCameraHold)

DT = 0.01
SPEED = 60 / 3.6


def rear(distance, limit=60):
  return {"type": 1, "distance": distance, "limit": limit, "rear": True}


def drive(hold, camera_at, start, total):
  """Drive from `start` m before the camera for `total` m; camera_at(dist) gives the event."""
  traveled, holding_from = 0.0, None
  while traveled < total:
    left = start - traveled
    if hold.update(camera_at(left), SPEED * DT) and holding_from is None:
      holding_from = traveled
    if holding_from is not None and hold.remaining <= 0.0:
      return holding_from, traveled
    traveled += SPEED * DT
  return holding_from, None


def test_front_camera_never_holds():
  hold = RearCameraHold()
  front = {"type": 1, "distance": 50.0, "limit": 60}
  assert not hold.update(front, 1.0)
  assert not hold.update(None, 1.0)
  assert hold.remaining == 0.0


def test_hold_starts_at_camera_and_lasts_hold_distance():
  hold = RearCameraHold()
  # Naver refreshes the distance only every 0.5 s (stale in between) and drops the
  # camera a little after passing it.
  last = {"left": None}

  def naver(left):
    if left <= -5.0:
      return None
    if last["left"] is None or last["left"] - left >= SPEED * 0.5:
      last["left"] = left
    return rear(max(last["left"], 1.0))

  start, end = drive(hold, naver, 300.0, 600.0)
  assert abs(start - 300.0) < 1.0
  assert abs((end - start) - REAR_CAMERA_HOLD_DIST) < 1.0
  assert hold.limit == 0.0


def test_dropped_close_to_camera_adds_remaining_distance():
  hold = RearCameraHold()
  hold.update(rear(30.0), 0.0)
  assert hold.update(None, 0.0)
  assert hold.remaining == REAR_CAMERA_HOLD_DIST + 30.0
  assert hold.limit == 60.0


def test_dropped_far_from_camera_does_not_hold():
  hold = RearCameraHold()
  hold.update(rear(REAR_CAMERA_LOST_ARM_DIST + 100.0), 0.0)
  assert not hold.update(None, 0.0)


def test_same_camera_is_not_rearmed_while_still_listed():
  hold = RearCameraHold()
  hold.update(rear(1.0), 0.0)
  assert hold.update(rear(1.0), 2.0)       # countdown passed the camera: hold armed
  remaining = hold.remaining
  assert hold.update(rear(1.0), 10.0)      # stale listing must not restart the hold
  assert hold.remaining == remaining - 10.0
  assert hold.update(None, 0.0)            # dropping it afterwards does not rearm either
  assert hold.remaining == remaining - 10.0
