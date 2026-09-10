"""C2 MixRadarInfo selection through the published radar-state interface."""
from types import SimpleNamespace

from selfdrive.controls.lib.radar_helpers import Cluster


def radar_state(radar_accel, vision_accel, model_prob=0.9, enabled=True, track_frames=10):
  cluster = Cluster()
  # A hashable track double supplies measured data without a native Kalman filter.
  track = type('TrackSample', (), {})()
  track.__dict__.update(cnt=track_frames, dRel=20.0, yRel=0.2, vRel=-1.0,
                        vLead=9.0, vLeadK=9.0, aLeadK=radar_accel, aLeadTau=1.5)
  cluster.add(track)
  lead = SimpleNamespace(prob=model_prob, a=[vision_accel], y=[0.8])
  return cluster.get_RadarState2(model_prob, lead, enabled)


def test_stronger_vision_acceleration_replaces_radar_and_shortens_tau():
  for radar, vision in ((-1.0, -3.0), (0.5, 1.5), (-1.5, 2.0)):
    state = radar_state(radar, vision)
    assert state['aLeadK'] == vision
    assert state['aLeadTau'] == 0.3
    assert state['radar'] and state['status']
    assert (state['dRel'], state['yRel'], state['vRel'], state['vLead']) == (20.0, 0.2, -1.0, 9.0)


def test_new_track_can_use_vision_without_old_blend_warmup():
  assert radar_state(0.05, -2.5, track_frames=1)['aLeadK'] == -2.5


def test_equal_or_stronger_radar_keeps_radar_acceleration_and_tau():
  for radar, vision in ((-3.0, -1.0), (-2.0, 2.0)):
    state = radar_state(radar, vision)
    assert state['aLeadK'] == radar
    assert state['aLeadTau'] == 1.5


def test_probability_threshold_and_disabled_mix_keep_radar():
  for options in ({'model_prob': 0.4}, {'model_prob': 0.5}, {'enabled': False}):
    state = radar_state(0.05, -2.5, **options)
    assert state['aLeadK'] == 0.05
    assert state['aLeadTau'] == 1.5
