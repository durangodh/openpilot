"""Start/stop a bounded N16 timing probe from the authenticated EON Web UI."""
import fcntl
import ipaddress
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/dev/shm')
REPORT = Path('/data/eon-n16-benchmark.json')


def read_json(path):
  try:
    with open(path, 'rb') as source:
      raw = source.read(65537)
    if len(raw) <= 65536:
      data = json.loads(raw)
      if isinstance(data, dict):
        return data
  except (OSError, ValueError, UnicodeError):
    pass
  return {}


class JetlinkBench:
  def __init__(self, basedir):
    self.basedir = str(basedir)
    self.process = None

  def snapshot(self):
    data = read_json(ROOT / 'eon-n16-status.json')
    alive = self.process is not None and self.process.poll() is None
    if self.process is not None and data.get('pid') != self.process.pid:
      return {'running': alive, 'stage': 'starting' if alive else 'error', 'driving_ready': False,
              'error': '' if alive else '시험을 시작하지 못했습니다. 차량 상태 또는 실행 중인 시험을 확인하세요.'}
    if data.get('running') and time.time() - data.get('updated_at', 0) > 5:
      data.update(running=False, stage='error', error='시험 상태 갱신이 중단되었습니다')
    if self.process is not None and not alive and data.get('running'):
      data.update(running=False, stage='error', error='시험 프로세스가 종료되었습니다')
    return data or {'running': False, 'stage': 'idle', 'driving_ready': False}

  def start(self, payload, blocked=None):
    if blocked:
      return 409, {'output': blocked}
    if not isinstance(payload, dict) or set(payload) != {'host', 'port', 'seconds', 'source'}:
      return 400, {'output': '시험 설정을 확인하세요'}
    try:
      host = ipaddress.ip_address(payload['host'])
      if host.version != 4 or not host.is_private or host.is_loopback or host.is_unspecified or host.is_multicast:
        raise ValueError()
      port, seconds, source = payload['port'], payload['seconds'], payload['source']
      if type(port) is not int or not 1024 <= port <= 65535 or type(seconds) is not int or seconds not in (60, 600) or source not in ('synthetic', 'camera'):
        raise ValueError()
    except (TypeError, ValueError):
      return 400, {'output': 'S20에 표시된 Wi-Fi IPv4 주소·포트와 시험 시간을 확인하세요'}
    if self.process is not None and self.process.poll() is None:
      return 409, {'output': '이미 시험 중입니다'}
    # Check the same OS lock used by the CLI; never start two model clients.
    with open(ROOT / 'eon-n16-lock', 'a') as lock:
      try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
      except OSError:
        return 409, {'output': '이미 다른 N16 시험이 실행 중입니다'}
    with open(ROOT / 'eon-n16-worker.log', 'wb') as output:
      self.process = subprocess.Popen(
        [sys.executable, '-m', 'selfdrive.modeld.jetlink.benchmark', '--host', str(host),
         '--port', str(port), '--seconds', str(seconds), '--source', source],
        cwd=self.basedir, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
        start_new_session=True, close_fds=True)
    return 202, {'output': 'N16 시험을 시작했습니다. 주행 제어는 기존 모델을 사용합니다.'}

  def stop(self):
    (ROOT / 'eon-n16-stop').touch()
    return 202, {'output': '시험 중지를 요청했습니다'}

  def report(self):
    data = read_json(REPORT)
    return data or {'output': '저장된 시험 결과가 없습니다'}
