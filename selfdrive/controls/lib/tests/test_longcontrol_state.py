from types import SimpleNamespace

from selfdrive.controls.lib.longcontrol import (LongCtrlState,
                                                long_control_state_trans)


def make_cp(openpilot_longitudinal=True, starting_state=False):
  return SimpleNamespace(
    enableGasInterceptor=False,
    openpilotLongitudinalControl=openpilot_longitudinal,
    vEgoStopping=0.3,
    vEgoStarting=0.2,
    startingState=starting_state,
  )


def transition(cp, state=LongCtrlState.stopping, v_target=0.0,
               v_target_1sec=0.0, brake_pressed=False, cruise_standstill=False,
               soft_hold=False, active=True, v_ego=0.0, a_target_now=0.0):
  next_state, _ = long_control_state_trans(
    cp, active, state, v_ego, v_target, v_target_1sec,
    brake_pressed, cruise_standstill, soft_hold, a_target_now,
  )
  return next_state


def test_small_planner_fluctuation_stays_stopped():
  assert transition(make_cp(), v_target=0.0, v_target_1sec=0.19) == LongCtrlState.stopping


def test_apilot_c2_launches_when_carstate_suppresses_stock_standstill():
  cp = make_cp()
  state = transition(cp, v_target=0.0, v_target_1sec=0.21)
  assert state == LongCtrlState.pid
  assert transition(cp, state=state, v_target=0.0, v_target_1sec=0.21) == LongCtrlState.pid


def test_start_accel_setting_uses_starting_state():
  cp = make_cp(starting_state=True)
  state = transition(cp, v_target=0.0, v_target_1sec=0.21)
  assert state == LongCtrlState.starting
  assert transition(cp, state=state, v_target=0.0, v_target_1sec=0.21) == LongCtrlState.starting


def test_stock_acc_still_waits_for_stock_standstill_to_clear():
  assert transition(make_cp(False), v_target=0.0, v_target_1sec=0.21,
                    cruise_standstill=True) == LongCtrlState.stopping


def test_brake_prevents_stale_standstill_override():
  assert transition(make_cp(), v_target=0.0, v_target_1sec=0.21,
                    brake_pressed=True) == LongCtrlState.stopping


def test_soft_hold_still_overrides_planner_launch():
  assert transition(make_cp(), v_target=0.0, v_target_1sec=0.21,
                    soft_hold=True) == LongCtrlState.stopping


def test_inactive_control_overrides_soft_hold():
  assert transition(make_cp(), soft_hold=True, active=False) == LongCtrlState.off


def test_strong_planned_braking_stays_in_pid_until_braking_eases():
  cp = make_cp()
  assert transition(cp, state=LongCtrlState.pid, a_target_now=-1.0) == LongCtrlState.pid
  assert transition(cp, state=LongCtrlState.pid, a_target_now=-0.99) == LongCtrlState.stopping


def test_starting_returns_to_pid_once_vehicle_is_moving():
  assert transition(make_cp(starting_state=True), state=LongCtrlState.starting,
                    v_ego=0.3, v_target=0.5, v_target_1sec=1.0) == LongCtrlState.pid


def test_starting_returns_to_stopping_when_plan_stops():
  assert transition(make_cp(starting_state=True), state=LongCtrlState.starting) == LongCtrlState.stopping


def test_launch_requires_speed_above_threshold_and_increasing_target():
  assert transition(make_cp(), v_target_1sec=0.2) == LongCtrlState.stopping
  assert transition(make_cp(), v_target=0.5, v_target_1sec=0.5) == LongCtrlState.stopping
