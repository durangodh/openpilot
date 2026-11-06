"""NOO waits while the navigation pose does not follow the car (no GPS)."""
from selfdrive.controls.lib.navigation_route import NavigationPoseTrust

DT = 0.05


def state(lat, lon, speed_kph=None, fresh=True):
  vehicle = {"lat": lat, "lon": lon}
  if speed_kph is not None:
    vehicle["speed_kph"] = speed_kph
  return {"route_fresh": fresh, "vehicle": vehicle}


def run(trust, seconds, v_ego, make):
  result = None
  for i in range(int(round(seconds / DT))):
    result = trust.update(make(i), v_ego, DT)
  return result


def moving(speed_mps, speed_kph=None):
  # metres per step along the latitude
  return lambda i: state(37.0 + i * speed_mps * DT / 111195.0, 127.0, speed_kph)


def test_moving_pose_with_matching_speed_is_trusted_after_recovery_time():
  trust = NavigationPoseTrust()
  assert run(trust, 1.0, 8.0, moving(8.0, 29.0)) is False
  assert run(trust, 1.5, 8.0, moving(8.0, 29.0)) is True


def test_frozen_position_while_driving_is_not_trusted():
  # Underground car park: the app keeps the last street position.
  trust = NavigationPoseTrust()
  run(trust, 3.0, 8.0, moving(8.0))
  assert trust.trusted
  assert run(trust, 2.5, 8.0, lambda i: state(37.1, 127.1)) is False


def test_app_speed_disagreeing_with_the_car_is_not_trusted():
  trust = NavigationPoseTrust()
  run(trust, 3.0, 8.0, moving(8.0, 29.0))
  assert trust.trusted
  # App says 0 km/h while the car drives at 29 km/h.
  assert run(trust, 1.2, 8.0, moving(8.0, 0.0)) is False


def test_standstill_with_stopped_pose_stays_trusted():
  trust = NavigationPoseTrust()
  run(trust, 3.0, 8.0, moving(8.0, 29.0))
  assert run(trust, 5.0, 0.0, lambda i: state(37.2, 127.2, 0.0)) is True


def test_stale_route_resets():
  trust = NavigationPoseTrust()
  run(trust, 3.0, 8.0, moving(8.0, 29.0))
  assert trust.update(state(37.0, 127.0, fresh=False), 8.0, DT) is False
