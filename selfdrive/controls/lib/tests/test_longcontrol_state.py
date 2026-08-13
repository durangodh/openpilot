from types import SimpleNamespace

from selfdrive.controls.lib.longcontrol import LongCtrlState, long_control_state_trans


def make_cp(openpilot_longitudinal=True):
  return SimpleNamespace(
    enableGasInterceptor=False,
    openpilotLongitudinalControl=openpilot_longitudinal,
    vEgoStopping=0.5,
    vEgoStarting=0.5,
  )


def transition(cp, state=LongCtrlState.stopping, v_target=0.0,
               v_target_1sec=0.0, brake_pressed=False, cruise_standstill=True,
               soft_hold=False):
  next_state, _ = long_control_state_trans(
    cp, True, state, 0.0, v_target, v_target_1sec,
    brake_pressed, cruise_standstill, soft_hold, 0.0, True,
  )
  return next_state


def test_stock_standstill_holds_through_small_planner_fluctuation():
  assert transition(make_cp(), v_target=0.2, v_target_1sec=0.3) == LongCtrlState.stopping


def test_planner_launch_overrides_stale_stock_standstill_for_openpilot_longitudinal():
  cp = make_cp()
  state = transition(cp, v_target=0.0, v_target_1sec=1.0)
  assert state == LongCtrlState.starting
  # The stock latch can remain set until acceleration is sent. It must not
  # force the state machine straight back to stopping on the next cycle.
  assert transition(cp, state=state, v_target=0.0, v_target_1sec=1.0) == LongCtrlState.starting


def test_stock_acc_still_waits_for_stock_standstill_to_clear():
  assert transition(make_cp(False), v_target=0.0, v_target_1sec=1.0) == LongCtrlState.stopping


def test_brake_prevents_stale_standstill_override():
  assert transition(make_cp(), v_target=0.0, v_target_1sec=1.0,
                    brake_pressed=True) == LongCtrlState.stopping


def test_soft_hold_still_overrides_planner_launch():
  assert transition(make_cp(), v_target=0.0, v_target_1sec=1.0,
                    soft_hold=True) == LongCtrlState.stopping
