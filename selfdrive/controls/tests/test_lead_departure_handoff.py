"""Run real LongControl/PID logic with cereal enums and Params isolated.

This is a controller regression test, not a vehicle or native MPC simulation.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from common.numpy_fast import clip, interp
from selfdrive.controls.lib.lead_departure import (LAUNCH_JERK_UPPER_MAX, LeadDepartureAssist,
                                                   departure_jerk_upper, lead_is_departing)
from selfdrive.controls.lib.pid import PIDController


def load_control():
  source = Path(__file__).resolve().parents[1] / 'lib' / 'longcontrol.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
  states = NS(off='off', pid='pid', stopping='stopping', starting='starting')
  car = NS(CarControl=NS(Actuators=NS(LongControlState=states)),
           CarState=NS(ButtonEvent=NS(Type=NS(accelCruise=1, resumeCruise=2))))
  env = dict(car=car, clip=clip, interp=interp, PIDController=PIDController,
             Params=lambda: NS(get=lambda *args, **kw: None), DT_CTRL=0.01,
             T_IDXS=[0.0, 0.5, 1.5], CONTROL_N=3,
             apply_deadzone=lambda error, dz: max(error - dz, 0.0) if error > 0 else min(error + dz, 0.0),
             LeadDepartureAssist=LeadDepartureAssist, lead_is_departing=lead_is_departing,
             departure_jerk_upper=departure_jerk_upper)
  exec(compile(tree, str(source), 'exec'), env)
  return env['LongControl'], env['long_control_state_trans']


def setup_control(starting=False):
  cls, _ = load_control()
  cp = NS(enableGasInterceptor=False, openpilotLongitudinalControl=True,
          vEgoStopping=0.3, vEgoStarting=0.2, startingState=starting,
          stoppingControl=True, stoppingDecelRate=1.0,
          longitudinalActuatorDelayLowerBound=0.5, longitudinalActuatorDelayUpperBound=0.5,
          longitudinalTuning=NS(kpBP=[0], kpV=[0.0], kiBP=[0], kiV=[0.0], kf=0.0,
                                deadzoneBP=[0], deadzoneV=[0]))
  control = cls(cp)
  control._read_params = lambda: None
  cp.startingState, cp.startAccel = starting, 0.25
  control.long_control_state = 'stopping'
  control.last_output_accel = -1.1
  cs = NS(vEgo=0.0, standstill=True, brakePressed=False, gasPressed=False,
          buttonEvents=[], cruiseState=NS(standstill=False))
  lead = NS(status=True, radar=True, dRel=7.0, vLeadK=0.0, vRel=0.0, aLeadK=0.0)
  radar = NS(leadOne=lead, leadTwo=NS(status=False), radarErrors=[])
  # Positive acceleration, but future speed is below the old 0.2 m/s gate.
  plan = NS(speeds=[0.0, 0.08, 0.18], accels=[0.0, 0.20, 0.0], jerks=[0.2]*3,
            trafficState=0, onStop=False, fcw=False, desiredDistance=6.0)
  return control, cs, plan, radar


def step(control, cs, plan, radar, fresh=True, **kw):
  return control.update(True, cs, plan, (-3.5, 2.0), 0.0, radar_state=radar,
                        radar_state_valid=True, radar_state_updated=fresh, **kw)[0]


@pytest.mark.parametrize('starting', [False, True])
def test_confirmed_departure_releases_below_old_speed_threshold(starting):
  control, cs, plan, radar = setup_control(starting)
  assert step(control, cs, plan, radar) < 0  # latch stationary lead
  radar.leadOne.vLeadK = radar.leadOne.vRel = 0.8
  radar.leadOne.aLeadK = 0.2
  assert step(control, cs, plan, radar) < 0  # only first fresh sample
  assert step(control, cs, plan, radar, fresh=False) < 0  # reread is not confirmation
  accel = step(control, cs, plan, radar)
  assert control.long_control_state == ('starting' if starting else 'pid')
  assert control.departure_assist.active
  # The hold releases at the launch jerk (assisted: LAUNCH_JERK_UPPER_MAX),
  # not at the stopping rate.
  assert accel == pytest.approx(-1.1 + LAUNCH_JERK_UPPER_MAX * 0.01)
  for _ in range(5):
    step(control, cs, plan, radar, fresh=False)
  assert control.long_control_state != 'stopping'
  # A new real stopping plan must cancel the window, not be held off for 1s.
  plan.speeds = [0.0]*3
  plan.accels = [-0.1]*3
  assert step(control, cs, plan, radar) < 0.25
  assert control.long_control_state == 'stopping'
  assert not control.departure_assist.active


@pytest.mark.parametrize('blocker', ['brake', 'soft_hold', 'stop', 'invalid_plan', 'second_lead', 'stock_acc'])
def test_existing_stop_guards_cannot_be_bypassed(blocker):
  control, cs, plan, radar = setup_control()
  step(control, cs, plan, radar)
  radar.leadOne.vLeadK = radar.leadOne.vRel = 0.8
  radar.leadOne.aLeadK = 0.2
  step(control, cs, plan, radar)
  kw = {}
  if blocker == 'brake':
    cs.brakePressed = True
  elif blocker == 'soft_hold':
    kw['soft_hold'] = True
  elif blocker == 'stop':
    plan.onStop = True
  elif blocker == 'invalid_plan':
    kw['plan_valid'] = False
  elif blocker == 'second_lead':
    radar.leadTwo = NS(status=True, radar=True, dRel=5.0, vLeadK=0.0, vRel=0.0, aLeadK=0.0)
  elif blocker == 'stock_acc':
    control.CP.openpilotLongitudinalControl = False
    cs.cruiseState.standstill = True
  assert step(control, cs, plan, radar, **kw) < 0
  assert control.long_control_state == 'stopping'
  assert not control.departure_assist.active


def test_floor_is_before_jerk_and_actuator_limits():
  control, cs, plan, radar = setup_control()
  step(control, cs, plan, radar)
  radar.leadOne.vLeadK = radar.leadOne.vRel = 0.8
  step(control, cs, plan, radar)
  step(control, cs, plan, radar)
  for _ in range(30):
    result = control.update(True, cs, plan, (-3.5, 0.1), 0.0,
                            radar_state=radar, radar_state_valid=True)[0]
    assert result <= 0.1


@pytest.mark.parametrize('failure', ['radar', 'plan_age', 'plan_shape', 'plan_invalid', 'lead_loss'])
def test_sensor_or_plan_failure_cancels_active_window(failure):
  control, cs, plan, radar = setup_control()
  step(control, cs, plan, radar)
  radar.leadOne.vLeadK = radar.leadOne.vRel = 0.8
  step(control, cs, plan, radar)
  step(control, cs, plan, radar)
  assert control.departure_assist.active
  if failure == 'plan_shape':
    plan.accels = []
  if failure == 'lead_loss':
    radar.leadOne.status = False
  control.update(True, cs, plan, (-3.5, 2.0), 0.21 if failure == 'plan_age' else 0.0,
                 radar_state=radar, radar_state_valid=failure != 'radar',
                 plan_valid=failure != 'plan_invalid')
  assert not control.departure_assist.active
  assert control.departure_assist.accel_floor == 0.0


def test_original_state_regressions_with_real_transition():
  # Run the existing state/gate test functions without importing cereal on Windows.
  cls, transition = load_control()
  source = Path(__file__).resolve().parents[1] / 'lib' / 'tests' / 'test_longcontrol_state.py'
  tree = ast.parse(source.read_text(encoding='utf-8'))
  tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
  env = dict(SimpleNamespace=NS, LongControl=cls, long_control_state_trans=transition,
             LongCtrlState=NS(off='off', pid='pid', stopping='stopping', starting='starting'),
             LEAD_DROPOUT_FALLBACK_FRAMES=150)
  exec(compile(tree, str(source), 'exec'), env)
  for name, test in list(env.items()):
    if name.startswith('test_'):
      test()


def configure_reported_start_stop(control):
  values = {'StartAccelApply': '40', 'StopAccelApply': '30',
            'StoppingDecelRate': '120', 'StandstillHoldApply': '55',
            'StandstillReleaseSpeed': '3', 'StandstillReleaseMs': '300'}
  control.params = NS(get=lambda key, **kw: values.get(key))
  control._update_start_stop_accel()
  control._update_stopping_decel_rate()
  control._update_standstill_hold()
  control._update_standstill_release()


def test_reported_stop_rate_applies_at_low_speed_and_through_hold():
  control, cs, plan, radar = setup_control()
  configure_reported_start_stop(control)
  assert control.CP.startAccel == pytest.approx(0.8)
  assert control.CP.stopAccel == pytest.approx(-0.6)
  assert control.standstill_hold_accel == pytest.approx(-1.1)
  control.long_control_state = 'pid'
  control.last_output_accel = -0.3
  cs.vEgo, cs.standstill = 0.2, False
  plan.speeds, plan.accels = [0.0]*3, [-0.3]*3
  previous = control.last_output_accel
  for frame in range(150):
    if frame == 50:
      cs.vEgo, cs.standstill = 0.0, True
    output = step(control, cs, plan, radar)
    assert control.long_control_state == 'stopping'
    assert abs(output - previous) <= 1.2 * 0.01 + 1e-10
    previous = output
    if frame == 49:
      assert output == pytest.approx(-0.6)
  assert output == pytest.approx(-1.1)


def test_stop_comfort_does_not_limit_pid_braking():
  control, cs, plan, radar = setup_control()
  configure_reported_start_stop(control)
  control.long_control_state = 'pid'
  control.last_output_accel = -2.0
  control.pid.k_f = 1.0
  cs.vEgo, cs.standstill = 5.0, False
  plan.speeds, plan.accels = [5.0, 3.5, 0.5], [-3.0]*3
  result = step(control, cs, plan, radar)
  assert control.long_control_state == 'pid'
  assert result < -2.03  # Original normal-driving braking jerk is preserved.


def setup_confirmed_start_handoff():
  control, cs, plan, radar = setup_control(True)
  configure_reported_start_stop(control)
  control.actuator_delay = 0.30
  control.pid_jerk_accel_mult, control.pid_jerk_decel_mult = 1.0, 1.1
  control.low_speed_jerk_boost = 1.4
  # A brisk START JERK LIMIT so startAccel is reached inside the 1 s window.
  control.jerk_start_limit = 3.0
  control.pid._k_p, control.pid._k_i = ([0], [0.55]), ([0], [0.10])
  control.pid.k_f = 0.95
  plan.speeds, plan.accels = [0.0, 0.2, 0.6], [0.4]*3
  step(control, cs, plan, radar)  # Confirm the initially stopped lead.
  radar.leadOne.vLeadK = radar.leadOne.vRel = 0.8
  radar.leadOne.aLeadK = 0.2
  step(control, cs, plan, radar)
  step(control, cs, plan, radar)
  for _ in range(300):
    if step(control, cs, plan, radar) >= 0.8:
      break
  assert control.long_control_state == 'starting'
  assert control.last_output_accel == pytest.approx(0.8)
  assert control.departure_assist.active
  cs.vEgo, cs.standstill = 0.21, False
  radar.leadOne.vRel = 0.59
  plan.speeds = [0.21, 0.41, 0.81]
  return control, cs, plan, radar


def test_confirmed_start_handoff_does_not_drop_drive_request():
  control, cs, plan, radar = setup_confirmed_start_handoff()
  values = [step(control, cs, plan, radar) for _ in range(40)]
  assert control.long_control_state == 'pid'
  # startAccel (0.8) eases down to the PID request at START_HANDOFF_JERK.
  assert values[0] == pytest.approx(0.8 - 1.6 * 0.01)
  assert all(0.38 - 1e-6 <= x <= 0.8 for x in values)
  assert all(0.0 <= a - b <= 1.6 * 0.01 + 1e-6 for a, b in zip(values, values[1:]))
  assert values[-1] == pytest.approx(0.38)


@pytest.mark.parametrize('veto', ['braking_plan', 'coasting_plan', 'lead_brake',
                                 'lead_loss', 'second_lead', 'stale_plan',
                                 'invalid_plan', 'radar_invalid', 'driver_brake',
                                 'driver_gas', 'traffic_stop', 'fcw', 'short_gap'])
def test_start_handoff_cancels_on_new_veto(veto):
  control, cs, plan, radar = setup_confirmed_start_handoff()
  step(control, cs, plan, radar)
  age, plan_valid, radar_valid = 0.0, True, True
  if veto == 'braking_plan':
    plan.speeds, plan.accels = [0.21, 0.16, 0.06], [-0.1]*3
  elif veto == 'coasting_plan':
    plan.speeds, plan.accels = [0.21]*3, [0.0]*3
  elif veto == 'lead_brake':
    radar.leadOne.aLeadK = -0.1
  elif veto == 'lead_loss':
    radar.leadOne.status = False
  elif veto == 'second_lead':
    radar.leadTwo = NS(status=True, radar=True, dRel=5.0, vLeadK=0.0, vRel=0.0, aLeadK=0.0)
  elif veto == 'stale_plan':
    age = 0.21
  elif veto == 'invalid_plan':
    plan_valid = False
  elif veto == 'radar_invalid':
    radar_valid = False
  elif veto == 'driver_brake':
    cs.brakePressed = True
  elif veto == 'driver_gas':
    cs.gasPressed = True
  elif veto == 'traffic_stop':
    plan.onStop = True
  elif veto == 'fcw':
    plan.fcw = True
  elif veto == 'short_gap':
    radar.leadOne.dRel = 3.0
  for _ in range(20):
    control.update(True, cs, plan, (-3.5, 2.0), age, radar_state=radar,
                   radar_state_valid=radar_valid, radar_state_updated=True, plan_valid=plan_valid)
    # The handoff exists only inside the assisted window; any veto closes it.
    assert not control.departure_assist.active


def test_start_handoff_never_slows_braking():
  control, cs, plan, radar = setup_confirmed_start_handoff()
  previous = step(control, cs, plan, radar)
  plan.speeds, plan.accels = [0.21, 0.0, 0.0], [-1.0]*3
  output = step(control, cs, plan, radar)
  assert not control.departure_assist.active
  assert previous - output > 1.6 * 0.01 + 1e-6  # normal PID braking jerk


def test_lowered_acceleration_cap_is_followed_with_jerk_limit():
  # A falling positive cap is not cut in one frame; PID eases down to it.
  control, cs, plan, radar = setup_confirmed_start_handoff()
  previous = control.last_output_accel
  for _ in range(100):
    output = control.update(True, cs, plan, (-3.5, 0.2), 0.0,
                            radar_state=radar, radar_state_valid=True, radar_state_updated=True)[0]
    assert 0.0 <= previous - output <= 3.5 * 1.1 * 0.01 + 1e-6
    previous = output
  assert output == pytest.approx(0.2)


def test_unconfirmed_pid_motion_never_uses_start_handoff():
  control, cs, plan, radar = setup_confirmed_start_handoff()
  control.departure_assist.reset()
  assert step(control, cs, plan, radar) == pytest.approx(0.7615)
  assert not control.departure_assist.active


def test_start_handoff_and_floor_expire_with_assist_window():
  control, cs, plan, radar = setup_confirmed_start_handoff()
  plan.accels = [0.05]*3  # Lower than the departure floor.
  for _ in range(120):
    output = step(control, cs, plan, radar)
    assert 0.0 <= output <= 0.8
  assert not control.departure_assist.active
  assert control.departure_assist.accel_floor == 0.0


def test_cancelled_assist_does_not_rearm_when_lead_recovers():
  control, cs, plan, radar = setup_confirmed_start_handoff()
  step(control, cs, plan, radar)
  radar.leadOne.aLeadK = -0.1
  step(control, cs, plan, radar)
  radar.leadOne.aLeadK = 0.2
  for _ in range(10):
    step(control, cs, plan, radar)
    assert not control.departure_assist.active
