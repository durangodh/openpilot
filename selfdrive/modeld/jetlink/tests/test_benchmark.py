import json
import sys
import time
from types import SimpleNamespace as NS
import numpy as np
import pytest
from selfdrive.modeld.jetlink import benchmark as B
from selfdrive.modeld.jetlink.tests.test_protocol import Phone
from selfdrive import eon_web_jetlink as W


def paths(monkeypatch, tmp_path):
  for key, name in [('STATUS','status.json'), ('STOP','stop'), ('LOCK','lock'), ('DEMAND','demand')]:
    monkeypatch.setattr(B, key, tmp_path / name)
  monkeypatch.setattr(B.signal, 'signal', lambda *args: None)


def test_benchmark_runs_through_tcp_and_saves_an_explicit_timing_only_report(monkeypatch, tmp_path):
  paths(monkeypatch, tmp_path)
  phone = Phone()
  args = NS(host='127.0.0.1', port=phone.port, seconds=0.2, source='synthetic',
            standalone=True, report=tmp_path/'report.json')
  try:
    assert B.run(args) == 0
  finally:
    phone.join()
  report = json.loads(args.report.read_text())
  assert report['stage'] == 'finished' and report['running'] is False
  assert report['frames'] > 0 and report['output_floats'] == 18580
  assert report['driving_ready'] is False and report['nav_map'] == 'blank'
  assert report['source'] == 'synthetic' and report['peer']['backend'] == 'mock'
  assert not B.DEMAND.exists()


def test_warmup_and_late_frames_do_not_become_driving_approval():
  stats = B.Metrics()
  for i in range(30):
    stats.add({'roundtrip_ms': 10, 'server_ms':8, 'gpu_ms':7}, 80 if i == 29 else 20)
  result = stats.snapshot()
  assert result['frames'] == 30 and result['samples'] == 10 and result['over_50ms'] == 1
  assert result['input_to_reply_max_ms'] == 80 and result['driving_ready'] is False
  json.dumps(result, allow_nan=False)


def test_timing_split_keeps_server_outside_time_distinct_from_network_time():
  stats = B.Metrics()
  timing = {'roundtrip_ms': 125, 'server_ms':37, 'gpu_ms':36, 'queue_ms':0.2,
            'pack_ms':1, 'send_ms':50, 'wait_ms':73, 'decode_ms':1,
            'check_ms':5, 'tx_bytes':393492}
  for _ in range(21):
    stats.add(timing, 130)
  result = stats.snapshot()
  assert result['samples'] == 1
  assert result['nonserver_mean_ms'] == 88
  assert result['send_mean_ms'] == 50 and result['wait_mean_ms'] == 73
  assert result['check_mean_ms'] == 5  # subset of send/wait, not added again
  assert result['request_mean_kib'] == 393492 / 1024
  assert result['driving_ready'] is False


@pytest.mark.parametrize('state', ['moving','drive','engaged','active','stale'])
def test_vehicle_guard_stops_in_motion_outside_park_or_without_valid_state(monkeypatch, state):
  class SubMaster:
    def __init__(self, *args):
      self.alive = {'carState': state != 'stale', 'controlsState': True}
      self.valid = {'carState':True, 'controlsState':True}
    def update(self, *_): pass
    def __getitem__(self,key):
      if key == 'carState':
        return NS(vEgo=1 if state == 'moving' else 0, gearShifter='drive' if state == 'drive' else 'park', canValid=True)
      return NS(enabled=state == 'engaged', active=state == 'active')
  monkeypatch.setitem(sys.modules, 'cereal', NS(messaging=NS(SubMaster=SubMaster)))
  guard = B.ParkGuard()
  with pytest.raises(B.Stopped):
    guard()


def test_web_controller_validates_ip_and_uses_argument_list_without_a_shell(monkeypatch, tmp_path):
  monkeypatch.setattr(W, 'ROOT', tmp_path)
  calls=[]
  class Process:
    pid = 456
    def poll(self): return None
  def spawn(args, **kwargs):
    calls.append((args,kwargs))
    return Process()
  monkeypatch.setattr(W.subprocess,'Popen',spawn)
  controller = W.JetlinkBench('/repo')
  payload={'host':'192.168.0.9','port':5599,'seconds':60,'source':'synthetic'}
  assert controller.start(payload, '인게이지 해제 필요')[0] == 409
  for bad in ['127.0.0.1','0.0.0.0','8.8.8.8','192.168.0.9; reboot']:
    assert controller.start(dict(payload,host=bad))[0] == 400
  assert calls == []
  assert controller.start(payload)[0] == 202
  assert calls[0][0][1:3] == ['-m','selfdrive.modeld.jetlink.benchmark']
  assert '--standalone' not in calls[0][0] and 'shell' not in calls[0][1]
  assert controller.start(payload)[0] == 409
  assert controller.snapshot()['stage'] == 'starting'
  assert controller.stop()[0] == 202
  assert (tmp_path/'eon-n16-stop').exists()
