from selfdrive.controls.lib.gap_recovery import LeadGapState, MAX_TOTAL_TF

DT = 0.05


def step(state, *, track=1, ego=20.0, lead=22.0, v_rel=2.0, d=45.0, desired=40.0, base_tf=1.4, enabled=True):
  return state.update(level=2, track_id=track, enabled=enabled, dt=DT, ego_speed=ego, lead_speed=lead,
                      relative_speed=v_rel, distance=d, desired_distance=desired, base_tf=base_tf, stop_distance=6.0)


def test_opening_gap_builds_bounded_headroom():
  s = LeadGapState()
  d, out = 45.0, []
  for _ in range(60):
    d += 2.0 * DT
    out.append(step(s, d=d))
  assert out[0] == 0.0 or out[0] < out[-1]
  assert 0.0 < out[-1] <= MAX_TOTAL_TF - 1.4


def test_headroom_is_released_when_gap_stops_opening():
  s = LeadGapState()
  d = 45.0
  for _ in range(60):
    d += 2.0 * DT
    peak = step(s, d=d)
  for _ in range(200):
    last = step(s, d=d, lead=20.0, v_rel=0.0)
  assert last < peak * 0.2


def test_disabled_or_new_lead_resets():
  s = LeadGapState()
  for _ in range(30):
    step(s, d=60.0)
  assert step(s, enabled=False) == 0.0
  assert s.key is None


def test_close_lead_gets_no_headroom():
  s = LeadGapState()
  assert max(step(s, d=30.0, desired=40.0) for _ in range(40)) == 0.0
