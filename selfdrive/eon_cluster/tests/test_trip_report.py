from selfdrive.eon_cluster.trip_report import TripReport


def _drive(trip, samples, t0=0.0, dt=0.05):
  t = t0
  for speed, accel, enabled, curv in samples:
    t += dt
    trip.update(t, True, speed, accel, enabled, curv)
  return t


def test_distance_speed_and_auto_ratio():
  trip = TripReport()
  trip.update(0.0, True, 0.0, 0.0, False, 0.0)
  _drive(trip, [(10.0, 0.0, True, 0.0)] * 200 + [(20.0, 0.0, False, 0.0)] * 100)  # 10 s + 5 s
  snap = trip.snapshot()
  assert snap["dist"] == 200
  assert snap["time"] == 15
  assert snap["max"] == 72.0
  assert snap["auto"] == 50
  assert abs(snap["avg"] - 200 / 15 * 3.6) < 0.1


def test_hard_events_count_once_per_episode():
  trip = TripReport()
  trip.update(0.0, True, 10.0, 0.0, True, 0.0)
  t = _drive(trip, [(10.0, 2.8, True, 0.0)] * 20)          # one hard accel lasting 1 s
  t = _drive(trip, [(10.0, 0.0, True, 0.0)] * 40, t)
  t = _drive(trip, [(10.0, -3.5, True, 0.0)] * 10, t)      # hard brake
  _drive(trip, [(10.0, 0.0, True, 0.035)] * 10, t)          # 3.5 m/s^2 lateral
  snap = trip.snapshot()
  assert (snap["hardAccel"], snap["hardBrake"], snap["hardCorner"]) == (1, 1, 1)
  assert snap["maxAccel"] == 2.8 and snap["maxDecel"] == -3.5


def test_resets_when_going_onroad_again():
  trip = TripReport()
  trip.update(0.0, True, 10.0, 0.0, True, 0.0)
  _drive(trip, [(10.0, 0.0, True, 0.0)] * 20)
  trip.update(5.0, False, 0.0, 0.0, False, 0.0)
  trip.update(6.0, True, 0.0, 0.0, False, 0.0)
  assert trip.snapshot()["dist"] == 0
