"""Run real LongControl/PID logic with cereal enums and Params isolated.

This is a controller regression test, not a vehicle or native MPC simulation.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from common.numpy_fast import clip, interp
from selfdrive.controls.lib.lead_departure import LeadDepartureAssist
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
             LeadDepartureAssist=LeadDepartureAssist)
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
  assert accel == pytest.approx(0.05 if starting else 0.02)
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
