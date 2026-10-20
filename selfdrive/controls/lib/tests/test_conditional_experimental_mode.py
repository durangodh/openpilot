"""Condition, timeout, priority and mode-release regressions, without EON natives."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

from selfdrive.controls.lib.conditional_experimental_mode import (
  ConditionalExperimentalMode, CURVE, LEAD, LOW_SPEED, NAVIGATION, select_longitudinal_mode)


def inputs(speed=60, lead=None, yaw=0):
  car = NS(vEgo=speed / 3.6)
  absent = NS(status=False)
  radar = NS(leadOne=NS(status=True, dRel=lead[0], vLead=lead[1] / 3.6) if lead else absent,
             leadTwo=absent)
  model = NS(orientationRate=NS(z=[yaw] * 33), position=NS(x=list(range(33))),
             velocity=NS(x=[car.vEgo] * 33))
  return car, radar, model


class TestConditionalExperimentalMode(unittest.TestCase):
  def setUp(self):
    self.c = ConditionalExperimentalMode()
    self.now = 10.0

  def tick(self, data=None, **kwargs):
    self.now += .05
    return self.c.update(self.now, kwargs.pop('enabled', True), kwargs.pop('available', True),
                         *(data or inputs()), **kwargs)

  def activate(self, data=None, navigation=None):
    for _ in range(12):
      reason = self.tick(data or inputs(10), navigation=navigation)
    self.assertNotEqual(reason, 0)
    return reason

  def test_actual_model_yaw_and_speed_and_lead_gates(self):
    condition = self.c.condition
    self.assertEqual(condition(*inputs(35, yaw=.06), None), CURVE)
    self.assertEqual(condition(*inputs(60, yaw=.06), None), 0)
    self.assertEqual(condition(*inputs(35, lead=(30, 35), yaw=.06), None), 0)
    self.assertEqual(condition(*inputs(35, yaw=-.06), None), CURVE)
    model = inputs(35)[2]
    model.orientationRate.z[-1] = .2
    self.assertEqual(condition(inputs(35)[0], inputs(35)[1], model, None), 0)

  def test_lead_uses_radar_absolute_speed_and_both_tracks(self):
    self.assertEqual(self.c.condition(*inputs(60, lead=(50, 50)), None), LEAD)
    self.assertEqual(self.c.condition(*inputs(60, lead=(81, 0)), None), 0)
    self.assertEqual(self.c.condition(*inputs(60, lead=(50, 65)), None), 0)
    car, radar, model = inputs(60)
    radar.leadTwo = NS(status=True, dRel=40, vLead=0)
    self.assertEqual(self.c.condition(car, radar, model, None), LEAD)

  def test_low_speed_depends_on_lead_presence(self):
    self.assertEqual(self.c.condition(*inputs(25), None), LOW_SPEED)
    self.assertEqual(self.c.condition(*inputs(25, lead=(100, 25)), None), 0)
    self.assertEqual(self.c.condition(*inputs(15, lead=(100, 15)), None), LOW_SPEED)
    self.assertEqual(self.c.condition(*inputs(30), None), 0)

  def test_navigation_rejects_straight_stale_offroute_and_bad_distance(self):
    nav = dict(fresh=True, off_route=False, kind='turn', distance=150)
    self.assertEqual(self.c.condition(*inputs(), nav), NAVIGATION)
    for changes in ({'fresh': False}, {'off_route': True}, {'kind': 'straight'},
                    {'distance': 201}, {'distance': float('nan')}, {'distance': -1}):
      self.assertEqual(self.c.condition(*inputs(), dict(nav, **changes)), 0)
    self.assertEqual(self.c.condition(*inputs(), dict(nav, distance=0)), NAVIGATION)

  def test_entry_requires_continuous_confirmed_condition(self):
    for _ in range(5):
      self.assertEqual(self.tick(inputs(10)), 0)
    self.tick(inputs(60))
    for _ in range(5):
      self.assertEqual(self.tick(inputs(10)), 0)
    for _ in range(7):
      self.tick(inputs(10))
    self.assertEqual(self.c.reason, LOW_SPEED)

  def test_clear_requires_minimum_active_time_then_returns_to_acc(self):
    self.activate()
    for _ in range(40):
      self.assertEqual(self.tick(inputs(60)), LOW_SPEED)
    for _ in range(80):
      self.tick(inputs(60))
    self.assertEqual(self.c.reason, 0)
    self.assertEqual(select_longitudinal_mode('acc', False, self.c.reason, False), ('acc', False))

  def test_data_loss_disable_or_driver_override_exits_without_five_second_wait(self):
    for kwargs in ({'enabled': False}, {'available': False}):
      self.c.reset()
      self.activate()
      self.assertEqual(self.tick(**kwargs), 0)
    for field in ('position', 'velocity', 'orientationRate'):
      self.c.reset()
      self.activate()
      data = inputs(10)
      getattr(data[2], field).__dict__['z' if field == 'orientationRate' else 'x'][0] = float('nan')
      self.assertEqual(self.tick(data), 0)

  def test_silent_planner_gap_cannot_keep_or_immediately_reenter_e2e(self):
    self.activate()
    self.now += 2
    self.assertEqual(self.tick(inputs(10)), 0)
    for _ in range(5):
      self.assertEqual(self.tick(inputs(10)), 0)

  def test_lost_or_finished_navigation_releases_immediately(self):
    nav = dict(fresh=True, kind='fork', distance=100)
    for replacement in (None, dict(nav, fresh=False), dict(nav, distance=1000),
                        dict(nav, kind='straight'), dict(nav, off_route=True)):
      self.c.reset()
      self.activate(inputs(), nav)
      self.assertEqual(self.tick(inputs(), navigation=replacement), 0)

  def test_signal_and_hold_and_existing_blended_mode_keep_priority(self):
    self.assertEqual(select_longitudinal_mode('acc', False, LEAD, True), ('acc', False))
    self.assertEqual(select_longitudinal_mode('blended', False, LEAD, False), ('blended', False))
    self.assertEqual(select_longitudinal_mode('acc', False, LEAD, False), ('blended', True))
    self.assertEqual(select_longitudinal_mode('acc', True, 0, False), ('blended', False))

  def test_repeated_override_never_contaminates_base_mode(self):
    base = 'acc'
    for _ in range(100):
      self.assertEqual(select_longitudinal_mode(base, False, CURVE, False)[0], 'blended')
    self.assertEqual(select_longitudinal_mode(base, False, 0, False)[0], 'acc')

  def test_mpc_production_handoff_uses_unmodified_apilot_base_and_stop_priority(self):
    source = Path(__file__).resolve().parents[1] / 'longitudinal_mpc_lib/long_mpc.py'
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LongitudinalMpc')
    update = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'update')
    # Execute the production handoff from update_apilot through mode selection.
    start = next(i for i, n in enumerate(update.body) if isinstance(n, ast.Assign) and
                 isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute) and
                 n.value.func.attr == 'update_apilot')
    handoff = compile(ast.Module(body=update.body[start:start+3], type_ignores=[]), str(source), 'exec')
    states = NS(e2eStop=3, softHold=4)
    mpc = NS(apilot_mode='acc', mode='acc', experimentalMode=False, xState=1)
    base_inputs = []
    def apilot(*args):
      base_inputs.append(args[-1])
      return 20, 1000, args[-1]
    mpc.update_apilot = apilot
    car = NS(gasPressed=False, brakePressed=False)
    controls = NS(enabled=True, longActiveUser=1, myDrivingMode=3)
    env = dict(self=mpc, controls=controls, carstate=car, radarstate=None, model=None,
               v_cruise=20, reset_state=False, conditional_reason=LEAD, XState=states,
               select_longitudinal_mode=select_longitudinal_mode)
    exec(handoff, env)
    self.assertEqual(mpc.mode, 'blended')
    env['conditional_reason'] = 0
    exec(handoff, env)
    self.assertEqual(mpc.mode, 'acc')
    self.assertEqual(base_inputs, ['acc', 'acc'])
    env['conditional_reason'] = LEAD
    for state in (3, 4):
      mpc.xState = state
      exec(handoff, env)
      self.assertEqual(mpc.mode, 'acc')
      self.assertFalse(mpc.conditionalActive)

  def test_planner_production_guards_and_no_navigation_reads_when_disabled(self):
    source = Path(__file__).resolve().parents[1] / 'longitudinal_planner.py'
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LongitudinalPlanner')
    update = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'update')
    start = next(i for i, n in enumerate(update.body) if isinstance(n, ast.Assign) and
                 isinstance(n.targets[0], ast.Name) and n.targets[0].id == 'conditional_available')
    block = compile(ast.Module(body=update.body[start:start+4], type_ignores=[]), str(source), 'exec')
    for gate in ('none', 'disabled', 'stock_long', 'reset', 'disengaged', 'long_off',
                 'fast', 'force_decel', 'gas', 'brake', 'stop', 'hold', 'stale'):
      with self.subTest(gate=gate):
        car, radar, model = inputs(10)
        car.gasPressed, car.brakePressed = gate == 'gas', gate == 'brake'
        controls = NS(enabled=gate != 'disengaged', longActiveUser=0 if gate == 'long_off' else 1)
        class Subscriptions(dict):
          def all_checks(self, service_list):
            return gate != 'stale'
        sm = Subscriptions(carState=car, radarState=radar, modelV2=model, controlsState=controls)
        reads, reasons = [], []
        mpc = NS(xState=3 if gate == 'stop' else 4 if gate == 'hold' else 1,
                 update=lambda *args, **kw: reasons.append(kw['conditional_reason']))
        planner = NS(CP=NS(openpilotLongitudinalControl=gate != 'stock_long'),
                     conditional_e2e_enabled=gate != 'disabled', mpc=mpc,
                     conditional_e2e=ConditionalExperimentalMode(),
                     conditional_navigation=NS(update=lambda: reads.append(True)))
        now = [10.0]
        env = dict(self=planner, sm=sm, reset_state=gate == 'reset',
                   myDrivingMode=4 if gate == 'fast' else 3, force_slow_decel=gate == 'force_decel',
                   XState=NS(e2eStop=3, softHold=4), time=NS(monotonic=lambda: now[0]),
                   v_cruise=10, x=[], v=[], a=[], j=[], y=[], prev_accel_constraint=True)
        for _ in range(12):
          now[0] += .05
          exec(block, env)
        if gate == 'none':
          self.assertEqual(reasons[-1], LOW_SPEED)
          self.assertEqual(len(reads), 12)
        else:
          self.assertEqual(reasons, [0] * 12)
          self.assertEqual(reads, [])

  def test_production_reason_badge_reports_effective_mode_only(self):
    source = Path(__file__).resolve().parents[1] / 'longitudinal_planner.py'
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LongitudinalPlanner')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'e2e_reason')
    env = {'XState': NS(e2eCruisePrepare=5, e2eStop=3)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), env)
    mpc = NS(mode='blended', experimentalMode=False, conditionalActive=True, xState=1)
    planner = NS(mpc=mpc, conditional_e2e=NS(reason=CURVE))
    sm = {'controlsState': NS(enabled=True)}
    self.assertEqual(env['e2e_reason'](planner, sm), CURVE)
    mpc.conditionalActive = False
    mpc.xState = 3
    self.assertEqual(env['e2e_reason'](planner, sm), 2)
    mpc.mode = 'acc'
    self.assertEqual(env['e2e_reason'](planner, sm), 1)
    sm['controlsState'].enabled = False
    self.assertEqual(env['e2e_reason'](planner, sm), 0)


if __name__ == '__main__':
  unittest.main()
