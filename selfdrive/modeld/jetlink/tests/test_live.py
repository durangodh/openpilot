import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np
import pytest

from selfdrive.modeld.jetlink import live_ipc as ipc
from selfdrive.modeld.jetlink.live import model_event
from selfdrive.modeld.jetlink.prediction import PredictionState

ROOT = Path(__file__).resolve().parents[4]


def converted(frame=100):
  return PredictionState().convert(np.zeros(18580), frame)


@pytest.fixture
def paths(monkeypatch, tmp_path):
  for key in ('CONFIG', 'CONTROL', 'STATE', 'STATUS', 'RESULT', 'DEMAND', 'FRAMES'):
    monkeypatch.setattr(ipc, key, tmp_path / key.lower())
  return tmp_path


def test_profile_disarms_first_and_old_revision_cannot_survive(tmp_path, monkeypatch):
  config, control = tmp_path / 'config', tmp_path / 'control'
  writes = []
  atomic = ipc.atomic
  def recording(path, data):
    atomic(path, data)
    writes.append((path, ipc.control(control)[1]))
  monkeypatch.setattr(ipc, 'atomic', recording)
  value = ipc.save_profile(dict(mode='external', host='auto', port=5600, compression=False), config, control)
  assert writes == [(control, False), (config, False), (control, True)]
  assert ipc.profile(config) == value
  previous = int(value['revision'])
  ipc.save_profile(dict(mode='native', host='auto', port=5600, compression=False), config, control)
  assert not ipc.control(control)[1] and ipc.control(control)[0] != previous


@pytest.mark.parametrize('payload', [b'', b'123', b'x' * (ipc.MAX_RESULT + 8)])
def test_result_body_bounds(payload):
  with pytest.raises(ValueError): ipc.encode_result(1, 2, 3, 4, 1., 1.01, 1, payload)


def test_result_rejects_late_response():
  with pytest.raises(ValueError): ipc.encode_result(1, 2, 3, 4, 1., 1.051, 1, b'12345678')


def test_native_state_freshness_and_actual_latch(tmp_path):
  path = tmp_path / 'state'
  path.write_bytes(ipc.STATE_HEADER.pack(b'ELS1', 1, 10_000_000_000, 100, 1 | 4 | 16 | 32,
                                        99, 2, 123, 2, 0, 9_990_000_000, 77))
  value = ipc.native_state(path, now=10.01)
  assert value['latched'] and not value['applied'] and value['native_available']
  assert ipc.native_state(path, now=10.3) is None
  assert not ipc.native_state(path, now=10.2)['native_available']


def test_web_native_selection_always_available(monkeypatch):
  from selfdrive.eon_web_live import LiveControl
  def save(value): return dict(value, revision='123')
  monkeypatch.setattr(ipc, 'save_profile', save)
  payload = dict(mode='native', host='auto', port=5600, compression=False)
  def forbidden(): raise AssertionError('Native does not require parked guard')
  assert LiveControl().save(payload, guard=forbidden)[0] == 200
  payload['mode'] = 'external'
  assert LiveControl().save(payload, guard=lambda: 'P단 정차 필요')[0] == 409


@pytest.mark.parametrize('change', [dict(host='8.8.8.8'), dict(host='127.0.0.1'), dict(port=True), dict(mode='bad'), dict(compression=1)])
def test_web_rejects_malformed_profile(change):
  from selfdrive.eon_web_live import LiveControl
  value = dict(mode='native', host='auto', port=5600, compression=False)
  value.update(change)
  assert LiveControl().save(value)[0] == 400


def test_web_does_not_claim_applied_from_worker_recommendation(monkeypatch):
  from selfdrive.eon_web_live import LiveControl
  monkeypatch.setattr(ipc, 'profile', lambda: dict(mode='external', revision='1'))
  monkeypatch.setattr(ipc, 'native_state', lambda: None)
  monkeypatch.setattr('selfdrive.eon_web_live.read_json', lambda _: dict(updated_at=time.time(), actual_source='external', applied=True))
  assert LiveControl().snapshot()['actual_source'] == 'native'


def test_model_event_serializes_actual_legacy_schema():
  from cereal import log
  raw = model_event(converted(), dict(frame_id=100, camera_timestamp_ns=123456))
  with log.Event.from_bytes(raw) as event:
    assert event.which() == 'modelV2' and event.valid
    assert event.modelV2.frameId == 100 and event.modelV2.timestampEof == 123456
    assert len(event.modelV2.position.x) == 33


@pytest.fixture(scope='session')
def native_publication_binary(tmp_path_factory):
  compiler = shutil.which('capnp')
  if not compiler:
    pytest.skip('capnp C++ compiler required for native publication integration test')
  output = tmp_path_factory.mktemp('publication') / 'test_live'
  generated = ROOT / 'cereal/gen/cpp'
  generated.mkdir(parents=True, exist_ok=True)
  subprocess.run([compiler, 'compile', '--src-prefix=cereal', '-oc++:cereal/gen/cpp',
                  'cereal/log.capnp', 'cereal/car.capnp', 'cereal/legacy.capnp'], cwd=ROOT, check=True)
  flags = os.environ.get('EONLINK_CPP_FLAGS', '').split()
  subprocess.run(['g++', '-std=c++17', '-O1', '-I.', *flags, str(Path(__file__).with_name('live_publication.cc')),
                  *[str(generated / (name + '.capnp.c++')) for name in ('log', 'car', 'legacy')],
                  '-lcapnp', '-lkj', '-o', str(output)], cwd=ROOT, check=True)
  return output


@pytest.fixture
def publisher(native_publication_binary, tmp_path):
  control, result, emitted = [tmp_path / name for name in ('control', 'result', 'emitted')]
  control.write_bytes(ipc.CONTROL_HEADER.pack(b'ELC1', 1, 77, 0, 1, 0))
  process = subprocess.Popen([str(native_publication_binary), str(control), str(result), str(emitted)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
  def call(frame=100, now=10.04, camera=123456, engaged=False, native=True, can=True, calibrated=True, send=True):
    process.stdin.write(f'{frame} {frame-1} {camera} {camera-1} {int(now*1e9)} 1 {int(native)} {int(can)} {int(calibrated)} {int(engaged)} {int(send)}\n')
    process.stdin.flush()
    return tuple(map(int, process.stdout.readline().split()))
  def packet(sequence=1, frame=100, started=10., completed=10.037, camera=123456, revision=77, session=88, model=None):
    raw = model_event(converted(frame) if model is None else model, dict(frame_id=frame, camera_timestamp_ns=camera))
    ipc.atomic(result, ipc.encode_result(revision, session, sequence, frame, started, completed, camera, raw))
  yield call, packet, control, result, emitted
  process.stdin.close(); process.wait(timeout=3)
  assert process.returncode == 0


def test_native_gate_actually_emits_external_cereal_and_falls_back(publisher):
  from cereal import log
  call, packet, _, _, emitted = publisher
  packet()
  assert call() == (1, 0, 1, 0, 5)
  with log.Event.from_bytes(emitted.read_bytes()) as event:
    assert event.which() == 'modelV2' and event.valid and event.modelV2.frameId == 100
    assert event.modelV2.timestampEof == 123456
    assert event.modelV2.modelExecutionTime == pytest.approx(.04)
  # A consumed response is never published twice, and loss while engaged latches.
  assert call(frame=101, now=10.09, camera=123457, engaged=True)[:4] == (0, 1, 0, 1)
  packet(sequence=2, frame=101, started=10.05, completed=10.087, camera=123457)
  assert call(frame=101, now=10.09, camera=123457, engaged=True)[:4] == (0, 1, 0, 1)
  assert call(frame=101, now=10.09, camera=123457)[:4] == (1, 0, 1, 0)


@pytest.mark.parametrize('case', ['revision', 'frame', 'timestamp', 'old', 'future', 'shape', 'nan', 'native', 'can', 'calibration', 'send', 'truncated', 'wrong_event', 'disabled'])
def test_native_publication_rejects_bad_or_unavailable_result(publisher, case):
  from cereal import log
  call, packet, control, result, _ = publisher
  kw, args = {}, {}
  if case == 'revision': kw['revision'] = 76
  if case == 'frame': args['frame'] = 102
  if case == 'timestamp': args['camera'] = 777
  if case == 'old': args['now'] = 10.101
  if case == 'future': args['now'] = 10.02
  if case in ('shape', 'nan'):
    model = converted()
    model['model']['position']['x'] = [0.] if case == 'shape' else [float('nan')] * 33
    kw['model'] = model
  if case in ('native', 'can', 'calibration'): args[{'calibration':'calibrated'}.get(case, case)] = False
  if case == 'send': args['send'] = False
  packet(**kw)
  if case == 'truncated': result.write_bytes(result.read_bytes()[:-8])
  if case == 'wrong_event':
    event = log.Event.new_message(valid=True); event.init('carState')
    result.write_bytes(ipc.encode_result(77,88,1,100,10.,10.037,123456,event.to_bytes()))
  if case == 'disabled': control.write_bytes(ipc.CONTROL_HEADER.pack(b'ELC1',1,78,0,0,0))
  state = call(**args)
  assert state[0] == 0 and state[1] == 1


def test_native_publication_session_change_requires_disengagement(publisher):
  call, packet, *_ = publisher
  packet(); assert call()[0] == 1
  packet(sequence=2, frame=101, started=10.05, completed=10.087, camera=123457, session=89)
  assert call(frame=101, camera=123457, now=10.09, engaged=True)[:4] == (0,1,0,1)
  assert call(frame=101, camera=123457, now=10.09)[:4] == (1,0,1,0)


def test_fallback_monitor_reads_native_latch_and_disarms_on_native_mode(paths):
  ipc.save_profile(dict(mode='external', host='auto', port=5600, compression=False))
  revision, _ = ipc.control()
  ipc.atomic(ipc.STATE, ipc.STATE_HEADER.pack(b'ELS1',1,10_000_000_000,100,1|2|4|16|32,99,1,88,2,0,9_990_000_000,revision))
  monitor = ipc.FallbackMonitor()
  assert monitor.update(10.01)
  ipc.save_profile(dict(mode='native', host='auto', port=5600, compression=False))
  assert not monitor.update(10.12)


@pytest.mark.parametrize('fault', ['none', 'slow', 'disable'])
def test_live_worker_creates_only_fresh_authorized_model_packets(paths, monkeypatch, fault):
  from selfdrive.modeld.jetlink import live
  config = ipc.save_profile(dict(mode='external', host='192.168.1.2', port=5600, compression=False))
  worker = live.LiveWorker()
  captured = []
  counter = [99]
  monkeypatch.setattr(live, 'LOCK', paths / 'lock')
  monkeypatch.setattr(live, 'actuator_times', lambda: (.2, .5))
  monkeypatch.setattr(live.socket, 'create_connection', lambda *a, **kw: object())
  monkeypatch.setattr(live, 'route_map', lambda: (bytes(65536), 'blank'))
  monkeypatch.setattr(ipc, 'native_state', lambda: dict(revision=config['revision'], frame_id=counter[0], applied=False,
                                                      latched=False, native_available=True, can_valid=True, calibrated=True, engaged=False))
  atomic = ipc.atomic
  def record(path, data):
    atomic(path, data)
    if path == ipc.RESULT: captured.append(data)
  monkeypatch.setattr(ipc, 'atomic', record)
  class Camera:
    def __init__(self, path): pass
    def close(self): pass
    def latest(self, last):
      counter[0] += 1
      if counter[0] >= 124: worker.stopped = True
      return dict(frame_id=counter[0], started=time.monotonic(), flags=3, camera_timestamp_ns=counter[0]*1000000,
                  desire=[0.]*8, traffic=[1.,0.], images=b'')
  class Phone:
    def __init__(self, *a): self.sequence = 0
    def close(self): pass
    def prepare(self): return {'model':'N16'}
    def infer(self, *a, **kw):
      self.sequence += 1
      if fault == 'slow': time.sleep(.055)
      if fault == 'disable': ipc.save_profile(dict(mode='native',host='auto',port=5600,compression=False))
      return np.zeros(18580), {}
  monkeypatch.setattr(live, 'Frames', Camera)
  monkeypatch.setattr(live, 'EonClient', Phone)
  with pytest.raises(live.Changed): worker.session(config)
  assert bool(captured) == (fault == 'none')
  assert not ipc.RESULT.exists() and not ipc.DEMAND.exists()
  if captured:
    for raw in captured:
      header = ipc.RESULT_HEADER.unpack_from(raw)
      assert header[2] == int(config['revision'])
      assert header[7] - header[6] <= 50_000_000
      from cereal import log
      with log.Event.from_bytes(raw[64:]) as event:
        assert event.which() == 'modelV2' and event.modelV2.frameId == header[5]


def test_authenticated_web_api_updates_real_selection_profile(paths):
  # Execute the real Handler in isolation from EON-only Params/video backends.
  import ast
  import hmac
  import http.client
  from http.server import BaseHTTPRequestHandler, HTTPServer
  import threading
  from types import SimpleNamespace
  from urllib.parse import urlsplit
  from selfdrive.eon_web_live import LiveControl
  source = ast.parse((ROOT / 'selfdrive/eon_web.py').read_text())
  node = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'Handler')
  scope = dict(BaseHTTPRequestHandler=BaseHTTPRequestHandler, json=json, hmac=hmac, urlsplit=urlsplit,
               ICON_FILES={}, subprocess=subprocess, trace_http=SimpleNamespace(is_trace_path=lambda _: False))
  exec(compile(ast.Module(body=[node], type_ignores=[]), 'eon_web.Handler', 'exec'), scope)
  server = HTTPServer(('127.0.0.1', 0), scope['Handler'])
  server.authorization = b'Basic test'
  controller = LiveControl()
  server.live_control = SimpleNamespace(snapshot=controller.snapshot,
    save=lambda payload: controller.save(payload, guard=lambda: 'P단 정차 필요'))
  thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
  connection = http.client.HTTPConnection(*server.server_address, timeout=3)
  def request(method, payload=None, auth=True, origin=True):
    headers = {'Content-Type':'application/json', 'X-EON-Action':'set-live-model'}
    if auth: headers['Authorization'] = 'Basic test'
    if origin: headers['Origin'] = 'http://127.0.0.1:' + str(server.server_port)
    connection.request(method, '/api/eonlink-live', body=json.dumps(payload) if payload else None, headers=headers)
    response = connection.getresponse(); body = response.read()
    return response.status, json.loads(body) if body else {}
  try:
    assert request('GET', auth=False)[0] == 401
    value = dict(mode='native',host='auto',port=5600,compression=False)
    assert request('POST', value, origin=False)[0] == 403
    assert request('POST', value)[0] == 200
    assert ipc.profile()['mode'] == 'native' and not ipc.control()[1]
    status, body = request('GET')
    assert status == 200 and body['actual_source'] == 'native'
    value['mode'] = 'external'
    assert request('POST', value)[0] == 409
    assert ipc.profile()['mode'] == 'native'
  finally:
    connection.close(); server.shutdown(); server.server_close(); thread.join(timeout=3)
