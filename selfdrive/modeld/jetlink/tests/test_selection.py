import copy
import threading
import time

import numpy as np
import pytest

from selfdrive.modeld.jetlink.prediction import PredictionState
from selfdrive.modeld.jetlink.selection import Limits, ModelSelector, Validation, valid_prediction
from selfdrive.modeld.jetlink.selection_monitor import SelectionMonitor
from selfdrive.modeld.jetlink.tests.test_prediction import output


@pytest.fixture
def converted():
  return PredictionState().convert(output(), 1)


def selector(frames=2):
  now = [10.]
  policy = ModelSelector(Limits(recovery_frames=frames), clock=lambda: now[0])
  policy.configure(True, Validation(True, True, True))
  policy.connect()
  return policy, now


def receive(policy, now, converted, seq, frame=None, duration=.03, real=True, calibrated=True):
  now[0] = 10. + seq * .05
  frame = seq if frame is None else frame
  ticket = policy.begin(seq, frame, now[0], real, calibrated)
  now[0] += duration
  accepted = policy.accept(ticket, frame, converted)
  return ticket, accepted


def test_external_selected_only_after_contiguous_recovery_and_explicit_validations(converted):
  policy, now = selector()
  ticket, accepted = receive(policy, now, converted, 1)
  assert accepted and policy.decide(1, True).source == 'native'
  ticket, accepted = receive(policy, now, converted, 2)
  decision = policy.decide(2, True)
  assert accepted and decision.source == 'external' and decision.ticket == ticket
  assert decision.ticket.frame_id == 2 and decision.ticket.session == policy.session
  policy.configure(True, Validation(True, False, True))
  receive(policy, now, converted, 3)
  receive(policy, now, converted, 4)
  assert policy.decide(4, True).reason == 'validation_pending'


def test_default_never_arms_control_even_with_healthy_real_camera_output(converted):
  now = [10.]
  policy = ModelSelector(Limits(recovery_frames=1), clock=lambda: now[0]); policy.connect()
  receive(policy, now, converted, 1)
  decision = policy.decide(1, True)
  assert decision.source == 'native' and decision.reason == 'validation_pending'
  assert not policy.enabled and not policy.validation.complete


def test_disabled_selector_remains_native_after_validated_healthy_response(converted):
  policy, now = selector(frames=1)
  policy.configure(False, Validation(True, True, True))
  receive(policy, now, converted, 1)
  assert policy.decide(1, True).reason == 'disabled'


@pytest.mark.parametrize('real,calibrated,reason', [(False, True, 'synthetic_input'), (True, False, 'uncalibrated')])
def test_synthetic_or_uncalibrated_input_never_becomes_an_external_candidate(converted, real, calibrated, reason):
  policy, now = selector(frames=1)
  _, accepted = receive(policy, now, converted, 1, real=real, calibrated=calibrated)
  decision = policy.decide(1, True)
  assert not accepted and decision.source == 'native' and decision.reason == reason


def test_late_reply_drops_previously_selected_external_model(converted):
  policy, now = selector(frames=1)
  receive(policy, now, converted, 1); assert policy.decide(1, True).source == 'external'
  _, accepted = receive(policy, now, converted, 2, duration=.051)
  decision = policy.decide(2, True, engaged=True)
  assert not accepted and decision.source == 'native' and decision.reason == 'late_response'
  assert policy.candidate is None and policy.healthy == 0 and policy.latch


def test_no_reply_expires_on_tick_without_waiting_for_the_socket(converted):
  policy, now = selector(frames=1)
  receive(policy, now, converted, 1); policy.decide(1, True)
  now[0] = 10.1
  ticket = policy.begin(2, 2, now[0], True, True)
  now[0] += .051
  decision = policy.decide(2, True, engaged=True)
  assert decision.source == 'native' and decision.reason == 'late_response'
  assert policy.pending is None and policy.candidate is None
  assert not policy.accept(ticket, 2, converted)


def test_disconnected_and_old_session_reply_cannot_replace_new_session_candidate(converted):
  policy, now = selector(frames=1)
  receive(policy, now, converted, 1); policy.decide(1, True)
  now[0] = 10.1; old = policy.begin(2, 2, now[0], True, True)
  policy.disconnect('link_error')
  assert policy.decide(2, True).source == 'native'
  policy.connect()
  current, accepted = receive(policy, now, converted, 1, frame=100)
  assert accepted
  assert not policy.accept(old, 2, converted)
  decision = policy.decide(100, True)
  assert decision.source == 'external' and decision.ticket == current


def test_mismatched_reply_frame_is_rejected(converted):
  policy, now = selector(frames=1)
  ticket = policy.begin(1, 9, now[0], True, True)
  assert not policy.accept(ticket, 10, converted)
  assert policy.decide(9, True).reason == 'frame_mismatch'


def test_future_frame_waits_for_native_instead_of_destroying_fresh_candidate(converted):
  policy, now = selector(frames=1)
  ticket, _ = receive(policy, now, converted, 1, frame=101)
  decision = policy.decide(100, True)
  assert decision.source == 'native' and decision.reason == 'waiting_native_frame'
  assert policy.candidate == ticket
  assert policy.decide(101, True).source == 'external'
  assert policy.decide(103, True).reason == 'frame_out_of_window'
  assert policy.candidate is None


def test_expired_result_is_not_repeated_to_fill_missing_frames(converted):
  policy, now = selector(frames=1)
  receive(policy, now, converted, 1); policy.decide(1, True)
  now[0] += .101
  assert policy.decide(1, True).reason == 'stale_result'
  assert policy.candidate is None


def test_frame_gap_restarts_recovery_and_uint32_wrap_is_contiguous(converted):
  policy, now = selector()
  receive(policy, now, converted, 1, frame=0xfffffffe)
  receive(policy, now, converted, 2, frame=0xffffffff)
  assert policy.decide(0xffffffff, True).source == 'external'
  receive(policy, now, converted, 3, frame=0)
  assert policy.decide(0, True).source == 'external'
  receive(policy, now, converted, 4, frame=2)
  assert policy.healthy == 1 and policy.decide(2, True).reason == 'warming_up'


@pytest.mark.parametrize('sequence,frame', [(1, 2), (2, 1), (2, 0), (0xffffffff, 2)])
def test_duplicate_reordered_requests_are_rejected(converted, sequence, frame):
  policy, now = selector(frames=1)
  receive(policy, now, converted, 1)
  assert policy.begin(sequence, frame, now[0], True, True) is None
  assert policy.decide(1, True).reason == 'request_order'


def test_only_one_request_in_flight_and_stale_input_rejected(converted):
  policy, now = selector(frames=1)
  assert policy.begin(1, 1, now[0], True, True)
  assert policy.begin(2, 2, now[0], True, True) is None
  assert policy.reason == 'overlapping_request'
  assert policy.begin(3, 3, now[0] - .051, True, True) is None
  assert policy.reason == 'stale_input'


def test_external_not_reentered_while_engaged_after_a_failure(converted):
  policy, now = selector(frames=1)
  receive(policy, now, converted, 1); policy.decide(1, True)
  policy.disconnect('link_error'); assert policy.decide(1, True, engaged=True).source == 'native'
  policy.connect(); receive(policy, now, converted, 1, frame=20)
  assert policy.decide(20, True, engaged=True).reason == 'recovery_latched'
  assert policy.decide(20, True, engaged=False).source == 'external'


def test_first_external_entry_cannot_happen_while_engaged(converted):
  policy, now = selector(frames=1)
  receive(policy, now, converted, 1)
  assert policy.decide(1, True, engaged=True).reason == 'wait_disengaged'
  assert policy.decide(1, True, engaged=False).source == 'external'


def test_unhealthy_native_is_not_used_as_a_fallback(converted):
  policy, now = selector(frames=1)
  receive(policy, now, converted, 1); policy.decide(1, True)
  decision = policy.decide(1, False, engaged=True)
  assert decision.source == 'none' and decision.disengage_required
  assert policy.candidate is None and policy.healthy == 0


@pytest.mark.parametrize('corruption', ['nan', 'width', 'probability', 'negative_std', 'time', 'lead', 'meta'])
def test_malformed_prediction_falls_back_without_consuming_bad_fields(converted, corruption):
  bad = copy.deepcopy(converted)
  if corruption == 'nan': bad['model']['position']['x'][0] = np.nan
  elif corruption == 'width': bad['model']['orientationRate']['z'].pop()
  elif corruption == 'probability': bad['model']['laneLineProbs'][0] = 1.1
  elif corruption == 'negative_std': bad['odometry']['transStd'][0] = -1
  elif corruption == 'time': bad['model']['velocity']['t'][2] = -.1
  elif corruption == 'lead': bad['model']['leadsV3'].pop()
  elif corruption == 'meta': bad['model']['meta']['desireState'] = [1.] * 8
  assert not valid_prediction(bad)
  policy, now = selector(frames=1)
  _, accepted = receive(policy, now, bad, 1)
  assert not accepted and policy.decide(1, True).reason == 'invalid_output'


def test_history_is_bounded_and_records_transition_reasons():
  policy, now = selector()
  for i in range(150):
    policy.connect(); policy.decide(1, True)
    policy.disconnect('link_error'); policy.decide(1, True)
  assert len(policy.events) == 64
  assert policy.snapshot(policy.decide(1, True))['history'][-1]['reason'] == 'link_error'


def test_independent_monitor_expires_when_request_thread_is_not_running(converted):
  policy, now = selector(frames=1)
  observed = threading.Event()
  native = [1]
  def factory():
    def read(): observed.set(); return native[0], True, False
    return read
  monitor = SelectionMonitor(factory, selector=policy)
  monitor.start()
  try:
    assert observed.wait(.5)
    monitor.connect()
    now[0] = 10.1
    ticket = monitor.begin(1, 1, now[0], True)
    now[0] += .03; assert monitor.accept(ticket, 1, converted)
    assert monitor.snapshot()['source'] == 'external'
    now[0] = 10.15; native[0] = 2
    monitor.begin(2, 2, now[0], True)
    # The thread calling begin/accept now stops doing work. The observer ticks.
    now[0] = 10.21
    deadline = time.monotonic() + .5
    while monitor.snapshot()['source'] != 'native' and time.monotonic() < deadline:
      threading.Event().wait(.005)
    state = monitor.snapshot()
    assert state['source'] == 'native' and state['reason'] == 'late_response'
    assert state['applied'] is False and state['mode'] == 'shadow'
  finally:
    monitor.close()


def test_observer_failure_reports_no_selection_instead_of_stale_health():
  def factory(): raise RuntimeError('observer unavailable')
  monitor = SelectionMonitor(factory)
  monitor.start()
  try:
    monitor.thread.join(.5)
    state = monitor.snapshot()
    assert state['source'] == 'none' and not state['native_available']
    assert state['observer_error'] == 'observer unavailable' and state['applied'] is False
  finally:
    monitor.close()


def test_camera_benchmark_records_selection_and_keeps_actuation_disabled(monkeypatch, tmp_path):
  import json
  from types import SimpleNamespace as NS
  from selfdrive.modeld.jetlink import benchmark as bench
  from selfdrive.modeld.jetlink import selection_monitor as monitors
  from selfdrive.modeld.jetlink.tests.test_benchmark import paths
  from selfdrive.modeld.jetlink.tests.test_protocol import Phone
  paths(monkeypatch, tmp_path)
  current = [0]
  class Frames:
    def latest(self, last):
      current[0] += 1
      return dict(frame_id=current[0], started=time.monotonic(), flags=3,
                  images=bytes(393216), desire=[0.] * 8, traffic=[1., 0.])
    def close(self): pass
  monkeypatch.setattr(bench, 'ParkGuard', lambda: lambda: None)
  monkeypatch.setattr(bench, 'Frames', Frames)
  original = monitors.SelectionMonitor
  monkeypatch.setattr(monitors, 'SelectionMonitor', lambda: original(lambda: lambda: (current[0], True, False)))
  phone = Phone()
  args = NS(host='127.0.0.1', port=phone.port, seconds=.4, source='camera', standalone=False,
            report=tmp_path / 'report.json')
  try:
    assert bench.run(args) == 0
  finally:
    phone.join()
  report = json.loads(args.report.read_text())
  choice = report['model_selection']
  assert choice['mode'] == 'shadow' and choice['applied'] is False
  assert choice['source'] == 'native' and not choice['enabled'] and not choice['validation_complete']
  assert choice['counts'].get('accepted', 0) > 0
  assert choice['reason'] == 'finished' and report['driving_ready'] is False
  candidate = json.loads(args.report.with_suffix('.candidate.json').read_text())
  assert candidate['model_selection']['mode'] == 'shadow' and candidate['applied'] is False
  assert not any(t.name == 'n16-selection-shadow' for t in threading.enumerate())
