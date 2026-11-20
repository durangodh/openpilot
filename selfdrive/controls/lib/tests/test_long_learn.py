"""LongLearn on simulated manual driving."""
import json

from selfdrive.controls.lib.long_learn import DT, LongLearn


def follow(learner, v, gap_t, stop_d, seconds, human=True):
  for _ in range(int(seconds / DT)):
    learner.update(human, v, 0.0, False, True, gap_t * v + stop_d, v)


def test_gap_learns_the_drivers_time_gap():
  ll = LongLearn()
  follow(ll, 12.0, 1.5, 5.0, 200)        # 43 km/h: no speed stretch
  follow(ll, 27.8, 1.5 * 1.2, 5.0, 200)  # 100 km/h: the planner stretches by 1.2
  r = ll.recommend(5.0, 3, 1.4, 1.2)
  assert abs(r["t_follow"] - 1.5) < 0.02 and r["TFollowGap3"] == 150


def test_nothing_while_the_car_drives_itself():
  ll = LongLearn()
  follow(ll, 20.0, 1.5, 5.0, 300, human=False)
  assert ll.recommend(6.0, 3, 1.4)["counts"]["gap"] == 0


def stop_and_go(ll, stop_gap):
  v, d = 10.0, 30.0
  while v > 0.0:                          # brake to a stop at 1.5 m/s^2 behind a stopped lead
    v = max(0.0, v - 1.5 * DT)
    d = max(stop_gap, d - v * DT)
    ll.update(True, v, -1.5, False, True, d, 0.0)
  for _ in range(int(2 / DT)):           # stand 2 s
    ll.update(True, 0.0, 0.0, False, True, stop_gap, 0.0)
  for _ in range(int(0.8 / DT)):         # lead moves off, the driver reacts 0.8 s later
    ll.update(True, 0.0, 0.0, False, True, stop_gap, 1.0)
  v = 0.0
  for _ in range(int(3 / DT)):
    v += 1.2 * DT
    ll.update(True, v, 1.2, True, True, stop_gap + 3, 3.0)


def test_stop_and_start():
  ll = LongLearn()
  for _ in range(6):
    stop_and_go(ll, 4.5)
  r = ll.recommend(6.0, 3, 1.4)
  assert r["StopDistance"] == 450
  assert abs(r["stop_decel_last_1s"] - 1.5) < 0.05
  assert 0.7 < r["start_delay_s"] < 1.4 and abs(r["start_accel"] - 1.2) < 0.05


def test_saved_round_trip():
  ll = LongLearn()
  follow(ll, 20.0, 1.5, 5.0, 60)
  stop_and_go(ll, 4.0)
  again = LongLearn(json.loads(json.dumps(ll.to_json())))
  assert json.dumps(again.to_json()) == json.dumps(ll.to_json())
