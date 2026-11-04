"""Radar Track Kalman reset rule (aPilot c2-master 0cd4072 / c3)."""
from types import SimpleNamespace

import selfdrive.controls.lib.radar_helpers as rh


class FakeKF:
  created = 0

  def __init__(self, x0, *_):
    FakeKF.created += 1
    self.x = [[x0[0][0]], [x0[1][0]]]

  def update(self, v):
    self.x[0][0] = v


def make_track(monkeypatch):
  monkeypatch.setattr(rh, 'KF1D', FakeKF)
  FakeKF.created = 0
  track = rh.Track(10.0, SimpleNamespace(A=None, C=None, K=None))
  track.update(30.0, 0.0, 0.0, 10.0, True)   # first frame always initialises
  return track


def test_speed_jump_on_the_same_target_keeps_the_filter(monkeypatch):
  track = make_track(monkeypatch)
  created = FakeKF.created
  # SCC speed noise / a braking lead: 1 m/s in one frame, distance continuous.
  track.update(29.8, 0.0, -1.0, 9.0, True)
  track.update(29.6, 0.0, -1.5, 8.5, True)
  assert FakeKF.created == created
  assert track.cnt == 3


def test_distance_jump_means_a_new_target(monkeypatch):
  track = make_track(monkeypatch)
  created = FakeKF.created
  track.update(18.0, 0.0, -2.0, 8.0, True)   # cut-in: 12 m closer
  assert FakeKF.created == created + 1
  assert track.cnt == 1
