"""Authenticated web control of the actual EON Link model source."""
import fcntl
import ipaddress
import math
import time

from selfdrive.modeld.jetlink import live_ipc as ipc
from selfdrive.eon_web_jetlink import read_json


def activation_block_reason():
  from cereal import messaging
  sm = messaging.SubMaster(['carState', 'controlsState'])
  deadline = time.monotonic() + 1.5
  while time.monotonic() < deadline:
    sm.update(100)
    if not all(sm.alive[k] and sm.valid[k] for k in ('carState', 'controlsState')):
      continue
    car, controls = sm['carState'], sm['controlsState']
    if controls.enabled or controls.active:
      return '외부 모델 선택은 인게이지를 해제한 뒤 실행하세요'
    if not car.canValid or not math.isfinite(car.vEgo):
      return '차량 CAN·속도 상태를 확인할 수 없습니다'
    return None
  return '차량·인게이지 상태를 확인할 수 없습니다'


class LiveControl:
  def snapshot(self):
    config = ipc.profile()
    state = ipc.native_state()
    worker = read_json(ipc.STATUS)
    if time.time() - worker.get('updated_at', 0) > 3:
      worker = dict(phase='worker_wait', error='시동 후 이온링크 연결 프로세스 시작을 기다리는 중')
    current = state and state['revision'] == config['revision']
    applied = bool(current and state['applied'] and ipc.control() == (int(config['revision']), True))
    return dict(config=config, native=state, worker=worker,
                actual_source='external' if applied else 'native', applied=applied,
                experimental=config['mode'] == 'external', camera_compatible=False)

  def save(self, payload, guard=activation_block_reason):
    if not isinstance(payload, dict) or set(payload) != {'mode', 'host', 'port', 'compression'}:
      return 400, {'output': '모델 설정을 확인하세요'}
    mode, host, port, compression = (payload[k] for k in ('mode', 'host', 'port', 'compression'))
    try:
      if mode not in ('native', 'external') or type(port) is not int or not 1024 <= port <= 65535 or type(compression) is not bool:
        raise ValueError()
      if host != 'auto':
        address = ipaddress.ip_address(host)
        if address.version != 4 or not address.is_private or address.is_loopback or address.is_multicast or address.is_unspecified:
          raise ValueError()
        host = str(address)
    except (TypeError, ValueError):
      return 400, {'output': '폰 IP·포트·모델 종류를 확인하세요'}
    if mode == 'external':
      reason = guard()
      if reason:
        return 409, {'output': reason}
      with open('/dev/shm/eon-n16-lock', 'a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
          # An already selected live worker owns the same lock. Reconfiguration
          # is safe because the new control revision disarms the old result.
          if ipc.profile()['mode'] != 'external':
            return 409, {'output': 'N16 연결·속도 시험을 먼저 중지하세요'}
    config = ipc.save_profile(dict(mode=mode, host=host, port=port, compression=compression))
    return 200, dict(output='기존 이온 모델을 선택했습니다' if mode == 'native' else
                     '외부 N16 시험 모드를 선택했습니다. 아래 실제 적용 상태를 확인하세요', config=config)
