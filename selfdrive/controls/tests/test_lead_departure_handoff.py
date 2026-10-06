"""Run real LongControl/PID logic with cereal enums and Params isolated.

This is a controller regression test, not a vehicle or native MPC simulation.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from common.numpy_fast import clip, interp
from selfdrive.controls.lib.lead_departure import (LeadDepartureAssist,
                                                   departure_jerk_upper, lead_is_creeping,
                                                   lead_is_departing, lead_raw_departing)
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
             departure_jerk_upper=departure_jerk_upper,
             lead_raw_departing=lead_raw_departing, lead_is_creeping=lead_is_creeping)
  exec(compile(tree, str(source), 'exec'), env)
  global LEAD_LAUNCH_JERK
  LEAD_LAUNCH_JERK = env['LEAD_LAUNCH_JERK']
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
  control.early_hold_relax = False   # 아래 출발 저크 검증은 미리 풀기 없이(별도 테스트 참고)
  assert step(control, cs, plan, radar) < 0  # latch stationary lead
  radar.leadOne.vLeadK = radar.leadOne.vRel = 0.8
  radar.leadOne.aLeadK = 0.2
  assert step(control, cs, plan, radar) < 0  # only first fresh sample
  assert step(control, cs, plan, radar, fresh=False) < 0  # reread is not confirmation
  accel = step(control, cs, plan, radar)
  assert control.long_control_state == ('starting' if starting else 'pid')
  assert control.departure_assist.active
  if starting:
    # apilot-c2: the starting state steps straight to startAccel; the launch
    # jerk goes to SCC14 instead (LEAD_LAUNCH_JERK for a departing lead).
    assert accel == pytest.approx(0.25)
    assert control.scc_launch_jerk() == pytest.approx(LEAD_LAUNCH_JERK)
  else:
    # Without a starting state the PID launch still leaves the hold at
    # LEAD_LAUNCH_JERK from the first frame.
    assert accel == pytest.approx(-1.1 + LEAD_LAUNCH_JERK * 0.01)
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


def test_no_lead_allowance_rises_gently_from_current_output():
  control, cs, plan, radar = setup_control(False)
  control.long_control_state = 'pid'
  cs.vEgo, cs.standstill = 10.0, False
  plan.speeds, plan.accels = [10.0, 10.0, 10.0], [0.0, 0.0, 0.0]
  step(control, cs, plan, radar)          # following a lead
  control.last_output_accel = 0.2
  radar.leadOne.status = False             # the lead is gone
  plan.speeds, plan.accels = [10.0, 13.0, 16.0], [2.0, 2.0, 2.0]
  accel = step(control, cs, plan, radar)
  # The allowance restarts at the current output, not at the full cap.
  assert control.pos_allowance == pytest.approx(0.2 + 0.5 * 0.01)
  assert accel <= control.pos_allowance + 1e-9
  for _ in range(100):
    step(control, cs, plan, radar)
  assert control.pos_allowance == pytest.approx(0.2 + 0.5 * 1.01, abs=1e-6)
  # A lead reappearing restores the full allowance at once.
  radar.leadOne.status = True
  step(control, cs, plan, radar)
  assert control.pos_allowance == pytest.approx(2.0)


def test_stopped_ego_latches_near_lead_with_jittery_speed():
  control, cs, plan, radar = setup_control(False)
  # Lead speed estimate jitters above 0.3 m/s while it is really stopped, and
  # the planner asks to close a small gap.
  radar.leadOne.vLeadK, radar.leadOne.vRel, radar.leadOne.aLeadK = 0.4, -0.05, 0.0
  plan.speeds, plan.accels = [0.0, 0.3, 0.6], [0.3, 0.3, 0.0]
  for _ in range(50):
    assert step(control, cs, plan, radar) < 0
  assert control.standstill_lead_latched
  assert control.long_control_state == 'stopping'


def test_no_lead_accel_release_is_eased_but_braking_is_not():
  def run(lead_status, plan_accel):
    control, cs, plan, radar = setup_control(False)
    radar.leadOne.status = lead_status
    if lead_status:
      radar.leadOne.dRel, radar.leadOne.vLeadK, radar.leadOne.vRel = 60.0, 15.0, 0.0
    control.long_control_state = 'pid'
    control.launch_time = 10.0
    control.last_output_accel = 0.5
    control.pid.k_f = 1.0
    cs.vEgo, cs.standstill = 15.0, False
    plan.speeds, plan.accels = [15.0, 15.0, 15.0], [plan_accel]*3
    first = step(control, cs, plan, radar)
    return 0.5 - first
  # Positive output easing down: no lead uses the softer release jerk.
  assert run(False, 0.0) == pytest.approx(1.2 * 0.01)
  assert run(True, 0.0) > 1.2 * 0.01 + 1e-6
  # Below zero the normal braking jerk returns on the next frame.
  control, cs, plan, radar = setup_control(False)
  radar.leadOne.status = False
  control.long_control_state, control.launch_time = 'pid', 10.0
  control.last_output_accel, control.pid.k_f = 0.005, 1.0
  cs.vEgo, cs.standstill = 15.0, False
  plan.speeds, plan.accels = [15.0, 14.0, 12.0], [-2.0]*3
  assert step(control, cs, plan, radar) == pytest.approx(0.0)
  assert step(control, cs, plan, radar) < -0.03


# ---- EarlyHoldRelax / FastLeadRelease ----

def _latch(control, cs, plan, radar):
  assert step(control, cs, plan, radar) < 0
  assert control.standstill_lead_latched


def _settle(control, cs, plan, radar, frames=100):
  for _ in range(frames):
    step(control, cs, plan, radar, fresh=False)


def test_early_hold_relax_eases_hold_before_release_and_restores_it():
  control, cs, plan, radar = setup_control()
  _latch(control, cs, plan, radar)
  _settle(control, cs, plan, radar)   # 완전히 선 뒤 1초
  # 꿈틀(원래 속도만 오름, 필터 속도 0): 출발 판정은 아니고 제동만 줄인다.
  radar.leadOne.vLead, radar.leadOne.vRel = 0.3, 0.2
  out = step(control, cs, plan, radar)
  for _ in range(5):
    out = step(control, cs, plan, radar, fresh=False)
    assert control.long_control_state == 'stopping'
  assert out == pytest.approx(-1.1 + 6 * 2.5 * 0.01)         # 2.5 m/s^3 로 줄인다
  for _ in range(30):
    out = step(control, cs, plan, radar, fresh=False)
    assert control.long_control_state == 'stopping'
  assert out == pytest.approx(-0.85)                          # 유지(-1.1)와 stopAccel(-0.6)의 중간까지만
  # 0.5초 동안 새 꿈틀 신호가 없으면 원래 유지 제동으로 천천히 돌아간다.
  radar.leadOne.vLead = radar.leadOne.vRel = 0.0
  for _ in range(200):
    prev = out
    out = step(control, cs, plan, radar, fresh=False)
    assert prev - out <= 1.0 * 0.01 + 1e-9                  # stopping_decel_rate 로만 내려간다
  assert out == pytest.approx(-1.1)
  assert control.long_control_state == 'stopping'


def test_early_hold_relax_waits_until_settled():
  control, cs, plan, radar = setup_control()
  _latch(control, cs, plan, radar)
  radar.leadOne.vLead, radar.leadOne.vRel = 0.3, 0.2
  for _ in range(50):   # 선 지 0.5초: 아직 풀지 않는다
    out = step(control, cs, plan, radar)
  assert out == pytest.approx(-1.1)
  cs.vEgo, cs.standstill = 0.03, False   # 조금 구르면 다시 센다
  step(control, cs, plan, radar)
  cs.vEgo, cs.standstill = 0.0, True
  for _ in range(90):
    out = step(control, cs, plan, radar)
  assert out == pytest.approx(-1.1)


def test_early_hold_relax_creep_restores_hold_and_disables_relax():
  control, cs, plan, radar = setup_control()
  _latch(control, cs, plan, radar)
  _settle(control, cs, plan, radar)
  radar.leadOne.vLead, radar.leadOne.vRel = 0.3, 0.2
  for _ in range(20):
    out = step(control, cs, plan, radar)
  assert out > -1.0                                     # 줄이는 중
  cs.vEgo, cs.standstill = 0.03, False                  # 크립
  prev = out
  out = step(control, cs, plan, radar)
  assert prev - out == pytest.approx(3.0 * 0.01)        # 빠르게 유지 제동으로
  cs.vEgo, cs.standstill = 0.0, True
  for _ in range(300):                                  # 꿈틀이 계속돼도 다시 풀지 않는다
    out = step(control, cs, plan, radar)
  assert out == pytest.approx(-1.1)
  assert control.long_control_state == 'stopping'


def test_early_hold_relax_gives_up_after_two_seconds():
  control, cs, plan, radar = setup_control()
  _latch(control, cs, plan, radar)
  _settle(control, cs, plan, radar)
  radar.leadOne.vLead, radar.leadOne.vRel = 0.3, 0.2
  for _ in range(400):   # 꿈틀만 4초 이어지고 출발은 안 됨
    out = step(control, cs, plan, radar)
    assert control.long_control_state == 'stopping'
  assert out == pytest.approx(-1.1)


def test_early_hold_relax_works_when_stop_accel_equals_hold():
  # STOP ACCEL 과 유지 제동이 같을 때(-1.4)도 꿈틀하면 제동을 줄인다.
  control, cs, plan, radar = setup_control()
  control.CP.stopAccel = -1.4
  control.standstill_hold_accel = -1.4
  control.last_output_accel = -1.4
  _latch(control, cs, plan, radar)
  _settle(control, cs, plan, radar)
  radar.leadOne.vLead, radar.leadOne.vRel = 0.3, 0.2
  out = step(control, cs, plan, radar)
  for _ in range(40):
    out = step(control, cs, plan, radar, fresh=False)
    assert control.long_control_state == 'stopping'
  assert out == pytest.approx(-1.4 + (-0.5 + 1.4) * 0.5)   # 유지(-1.4)와 -0.5 의 중간


def test_early_hold_relax_off_keeps_hold():
  control, cs, plan, radar = setup_control()
  control.early_hold_relax = False
  _latch(control, cs, plan, radar)
  _settle(control, cs, plan, radar)
  radar.leadOne.vLead, radar.leadOne.vRel = 0.3, 0.2
  for _ in range(50):
    out = step(control, cs, plan, radar)
  assert out == pytest.approx(-1.1)


@pytest.mark.parametrize('fast', [True, False])
def test_fast_lead_release_uses_raw_radar_speed(fast):
  control, cs, plan, radar = setup_control()
  control.fast_lead_release = fast
  _latch(control, cs, plan, radar)
  # 필터 속도(0.15)는 기존 기준(0.25) 미만, 원래 속도는 출발 중.
  radar.leadOne.vLead, radar.leadOne.vLeadK, radar.leadOne.vRel = 0.5, 0.15, 0.5
  radar.leadOne.aLeadK = 0.2
  step(control, cs, plan, radar)
  step(control, cs, plan, radar)
  assert (control.long_control_state != 'stopping') == fast


def test_fast_lead_release_ignores_vision_only_lead_and_filtered_spike():
  control, cs, plan, radar = setup_control()
  _latch(control, cs, plan, radar)
  radar.leadOne.vLead, radar.leadOne.vLeadK, radar.leadOne.vRel = 0.5, 0.05, 0.5
  for _ in range(3):   # 필터 속도가 아직 0 근처: 튀는 원래 속도 하나로는 출발하지 않는다
    step(control, cs, plan, radar)
  assert control.long_control_state == 'stopping'
  radar.leadOne.vLeadK, radar.leadOne.radar = 0.15, False
  for _ in range(3):   # 카메라 전용 앞차는 원래 속도로 출발 판정하지 않는다
    step(control, cs, plan, radar)
  assert control.long_control_state == 'stopping'


# ---- EarlyStopReqRelease (C) ----

def test_early_stopreq_release_off_by_default():
  control, cs, plan, radar = setup_control()
  _latch(control, cs, plan, radar)
  _settle(control, cs, plan, radar)
  radar.leadOne.vLead, radar.leadOne.vRel = 0.3, 0.2
  step(control, cs, plan, radar)
  assert not control.stopreq_release_active


def test_early_stopreq_release_window_keeps_brake_and_times_out():
  control, cs, plan, radar = setup_control()
  control.early_stopreq_release = True
  control.early_hold_relax = False
  _latch(control, cs, plan, radar)
  radar.leadOne.vLead, radar.leadOne.vRel = 0.3, 0.2
  step(control, cs, plan, radar)
  assert not control.stopreq_release_active          # 선 지 1초 전에는 안 한다
  radar.leadOne.vLead = radar.leadOne.vRel = 0.0
  _settle(control, cs, plan, radar)
  radar.leadOne.vLead, radar.leadOne.vRel = 0.3, 0.2
  out = step(control, cs, plan, radar)
  assert control.stopreq_release_active
  assert out == pytest.approx(-1.1)                  # 브레이크 명령은 그대로
  radar.leadOne.vLead = radar.leadOne.vRel = 0.0
  for _ in range(80):
    step(control, cs, plan, radar, fresh=False)
  assert not control.stopreq_release_active          # 0.8초 안에 출발 안 하면 StopReq 복귀
  assert control.long_control_state == 'stopping'


def test_early_stopreq_release_movement_blocks_it_for_this_stop():
  control, cs, plan, radar = setup_control()
  control.early_stopreq_release = True
  _latch(control, cs, plan, radar)
  _settle(control, cs, plan, radar)
  radar.leadOne.vLead, radar.leadOne.vRel = 0.3, 0.2
  step(control, cs, plan, radar)
  assert control.stopreq_release_active
  cs.vEgo, cs.standstill = 0.03, False
  step(control, cs, plan, radar)
  assert not control.stopreq_release_active
  cs.vEgo, cs.standstill = 0.0, True
  for _ in range(300):
    step(control, cs, plan, radar)
  assert not control.stopreq_release_active


def test_early_stopreq_release_ends_with_brake_or_launch():
  control, cs, plan, radar = setup_control()
  control.early_stopreq_release = True
  _latch(control, cs, plan, radar)
  _settle(control, cs, plan, radar)
  radar.leadOne.vLead, radar.leadOne.vRel = 0.3, 0.2
  step(control, cs, plan, radar)
  cs.brakePressed = True
  step(control, cs, plan, radar)
  assert not control.stopreq_release_active
  cs.brakePressed = False
  # 실제 출발: 상태가 stopping 을 벗어나면 꺼진다.
  radar.leadOne.vLead, radar.leadOne.vLeadK, radar.leadOne.vRel, radar.leadOne.aLeadK = 0.8, 0.8, 0.8, 0.2
  for _ in range(3):
    step(control, cs, plan, radar)
  assert control.long_control_state != 'stopping'
  assert not control.stopreq_release_active


def test_scc_launch_jerk_only_while_launching():
  control, cs, plan, radar = setup_control(starting=True)
  control.jerk_start_limit = 1.0
  assert control.scc_launch_jerk() is None              # stopping: SCC14 keeps hold limits
  control.long_control_state, control.lead_launch, control.launch_time = 'starting', False, 0.01
  assert control.scc_launch_jerk() == pytest.approx(1.0)
  control.long_control_state, control.launch_time = 'pid', 2.0
  assert control.scc_launch_jerk() == pytest.approx(1.0 + (5.0 - 1.0) * 0.5)
  control.launch_time = 3.0
  assert control.scc_launch_jerk() is None              # launch over: generous limits again


def test_delayed_scc_release_keeps_full_rolling_launch_window():
  control, cs, plan, radar = setup_confirmed_start_handoff()
  cs.vEgo, cs.standstill = 0.0, True
  control.jerk_start_limit = 1.0
  for _ in range(130):  # recorded SCC release delay: about 1.3 seconds
    step(control, cs, plan, radar)
  assert control.launch_time == pytest.approx(0.01)
  assert not control.launch_motion_started
  assert control.scc_launch_jerk() == pytest.approx(LEAD_LAUNCH_JERK)
  cs.vEgo, cs.standstill = 0.21, False
  plan.speeds, plan.accels = [0.21, 1.0, 2.0], [1.6]*3
  previous = control.last_output_accel
  for _ in range(100):
    output = step(control, cs, plan, radar)
    assert output - previous <= 1.0 * 0.01 + 1e-9
    previous = output
  assert control.launch_motion_started
  assert control.launch_time < 1.5
  assert control.scc_launch_jerk() == pytest.approx(1.0)


def test_starting_respects_approach_acceleration_cap():
  control, cs, plan, radar = setup_confirmed_start_handoff()
  cs.vEgo, cs.standstill = 0.0, True
  for cap in (0.2, 0.0):
    result = control.update(True, cs, plan, (-3.5, cap), 0.0,
                            radar_state=radar, radar_state_valid=True,
                            radar_state_updated=True)[0]
    assert result <= cap
