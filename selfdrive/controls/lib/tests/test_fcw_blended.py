"""FcwBlendedGuard: DEC-style switch from ACC to blended while the MPC predicts a crash."""
from selfdrive.controls.lib.conditional_e2e import FCW_BLEND_HOLD_TIME, FcwBlendedGuard

DT = 0.05


def run(guard, crash, frames, enabled=True, standstill=False):
  return [guard.update(enabled, crash, standstill) for _ in range(frames)]


def test_single_crash_frame_is_ignored():
  g = FcwBlendedGuard(DT)
  assert run(g, 0, 10) == [False] * 10
  assert g.update(True, 1, False) is False
  assert run(g, 0, 10) == [False] * 10


def test_sustained_crash_switches_then_holds_and_releases():
  g = FcwBlendedGuard(DT)
  out = run(g, 3, 5)
  assert out[:2] == [True, True]          # empty window fills: 1/1, 2/2 >= 0.6
  assert all(out)
  after = run(g, 0, 30)
  hold = round(FCW_BLEND_HOLD_TIME / DT)
  # the window still holds crash frames for two more ticks, then the hold counts down
  assert all(after[:hold])
  assert not any(after[-5:])


def test_disabled_or_standstill_never_switches():
  assert run(FcwBlendedGuard(DT), 5, 20, enabled=False) == [False] * 20
  assert run(FcwBlendedGuard(DT), 5, 20, standstill=True) == [False] * 20


def test_standstill_resets_hold():
  g = FcwBlendedGuard(DT)
  run(g, 3, 5)
  assert g.update(True, 0, True) is False
  assert g.update(True, 0, False) is False
