import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from selfdrive.eon_cluster.section_average import SectionAverage  # noqa: E402


def drive(tracker, section, v_ego, seconds, start=0.0, dt=0.1):
  value, t = 0.0, start
  for _ in range(int(round(seconds / dt))):
    value = tracker.update(section, v_ego, t)
    section = dict(section, distance=max(1.0, section["distance"] - v_ego * dt))
    t += dt
  return value, section, t


def test_average_is_distance_over_time_after_entry():
  tracker = SectionAverage()
  section = {"distance": 5000.0, "limit": 100.0, "average": 0.0}
  assert tracker.update(section, 25.0, 0.0) == 0.0
  value, section, t = drive(tracker, section, 25.0, 10.0, start=0.1)
  assert value == pytest.approx(90.0, rel=0.02)
  value, _, _ = drive(tracker, section, 20.0, 10.0, start=t)
  assert value == pytest.approx(81.0, rel=0.03)


def test_hidden_until_enough_time_has_passed():
  tracker = SectionAverage()
  value, _, _ = drive(tracker, {"distance": 3000.0, "limit": 80.0}, 22.0, 2.0)
  assert value == 0.0


def test_app_reported_average_wins():
  tracker = SectionAverage()
  section = {"distance": 3000.0, "limit": 80.0, "average": 77.0}
  assert tracker.update(section, 30.0, 0.0) == 77.0


def test_leaving_or_new_section_restarts():
  tracker = SectionAverage()
  _, section, t = drive(tracker, {"distance": 3000.0, "limit": 80.0}, 30.0, 10.0)
  assert tracker.update(None, 30.0, t) == 0.0
  assert tracker.started_at is None
  _, section, t = drive(tracker, {"distance": 3000.0, "limit": 80.0}, 30.0, 5.0, start=t)
  # Remaining distance jumps up: a following section, so the average restarts.
  assert tracker.update(dict(section, distance=4000.0), 10.0, t) == 0.0
  assert tracker.distance_m == 0.0
