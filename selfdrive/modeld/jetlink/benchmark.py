"""Measure N16 on a phone, with synthetic input or live EON prepared frames.

Never publishes modelV2, cameraOdometry, carControl or any actuation result.
"""
import argparse
import fcntl
import ipaddress
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path

import numpy as np

from selfdrive.modeld.jetlink.client import Client, LinkError, SPEC, WARPED_BYTES, navigation_input
from selfdrive.modeld.jetlink.frames import DEMAND, Frames, route_map
from selfdrive.modeld.jetlink.prediction import PredictionState

STATUS = Path('/dev/shm/eon-n16-status.json')
STOP = Path('/dev/shm/eon-n16-stop')
LOCK = Path('/dev/shm/eon-n16-lock')
REPORT = Path('/data/eon-n16-benchmark.json')


class Stopped(RuntimeError):
  pass


def atomic_json(path, value):
  path = Path(path)
  temp = path.with_name(path.name + '.tmp')
  path.parent.mkdir(parents=True, exist_ok=True)
  with open(temp, 'w') as stream:
    os.chmod(str(temp), 0o600)
    json.dump(value, stream, allow_nan=False)
  os.replace(str(temp), str(path))


def address(value):
  ip = ipaddress.ip_address(value)
  if ip.version != 4 or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
    raise ValueError('휴대폰에 표시된 IPv4 주소를 입력하세요')
  return str(ip)


class ParkGuard:
  def __init__(self):
    from cereal import messaging
    self.sm = messaging.SubMaster(['carState', 'controlsState'])
    self.speed = 0.0

  def __call__(self):
    self.sm.update(0)
    if not all(self.sm.alive[k] and self.sm.valid[k] for k in ('carState', 'controlsState')):
      raise Stopped('차량 상태 수신 대기: 시동을 켜고 P단에서 시험하세요')
    car, controls = self.sm['carState'], self.sm['controlsState']
    self.speed = float(car.vEgo)
    if not car.canValid or not np.isfinite(self.speed) or abs(self.speed) >= 0.1 or str(car.gearShifter) != 'park' or controls.enabled or controls.active:
      raise Stopped('P단 정차·인게이지 해제 상태에서만 시험합니다')


class Metrics:
  def __init__(self):
    self.frames = self.skipped = 0
    self.rows = []

  def add(self, timings, age_ms):
    self.frames += 1
    # Exclude engine warm-up from percentiles but retain its count.
    if self.frames > 20:
      self.rows.append([timings['roundtrip_ms'], timings['server_ms'], timings['gpu_ms'], age_ms] +
                       [timings.get(key, 0.0) for key in
                        ('pack_ms', 'send_ms', 'wait_ms', 'decode_ms', 'check_ms', 'tx_bytes', 'queue_ms',
                         'phone_receive_ms', 'phone_inflate_ms', 'phone_engine_ms', 'plain_tx_bytes', 'compress_ms')])

  def snapshot(self):
    result = {'frames': self.frames, 'samples': len(self.rows), 'skipped_source_frames': self.skipped,
              'driving_ready': False, 'warmup_frames': min(self.frames, 20)}
    if self.rows:
      a = np.asarray(self.rows)
      result.update({'roundtrip_mean_ms': float(a[:, 0].mean()),
                     'roundtrip_p99_ms': float(np.percentile(a[:, 0], 99)),
                     'roundtrip_max_ms': float(a[:, 0].max()),
                     'input_to_reply_mean_ms': float(a[:, 3].mean()),
                     'server_mean_ms': float(a[:, 1].mean()), 'gpu_mean_ms': float(a[:, 2].mean()),
                     'input_to_reply_p99_ms': float(np.percentile(a[:, 3], 99)),
                     'input_to_reply_max_ms': float(a[:, 3].max()),
                     'over_50ms': int((a[:, 3] > 50).sum())})
      for column, name in enumerate(('pack', 'send', 'wait', 'decode', 'check'), start=4):
        result[name + '_mean_ms'] = float(a[:, column].mean())
      result['send_p99_ms'] = float(np.percentile(a[:, 5], 99))
      result['wait_p99_ms'] = float(np.percentile(a[:, 6], 99))
      result['request_mean_kib'] = float(a[:, 9].mean() / 1024)
      result['queue_mean_ms'] = float(a[:, 10].mean())
      for column, name in enumerate(('phone_receive', 'phone_inflate', 'phone_engine'), start=11):
        result[name + '_mean_ms'] = float(a[:, column].mean())
      result['plain_request_mean_kib'] = float(a[:, 14].mean() / 1024)
      result['compress_mean_ms'] = float(a[:, 15].mean())
      result['nonserver_mean_ms'] = float((a[:, 0] - a[:, 1]).mean())
    return result


def run(args):
  lock = open(LOCK, 'a')
  try:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
  except OSError:
    lock.close()
    raise RuntimeError('이미 N16 시험이 실행 중입니다')
  # A second caller must not remove the active process's stop request.
  STOP.unlink(missing_ok=True)
  stopped = [False]
  signal.signal(signal.SIGTERM, lambda *_: stopped.__setitem__(0, True))
  signal.signal(signal.SIGINT, lambda *_: stopped.__setitem__(0, True))
  guard = None if args.standalone else ParkGuard()
  frames = Frames()
  client = None
  statistics = Metrics()
  prediction = PredictionState()
  candidate = None
  selection = None
  transport = getattr(args, 'transport', 'jetlink')
  status = {'running': True, 'stage': 'connecting', 'source': args.source, 'host': args.host,
            'port': args.port, 'model_sha256': SPEC['sha256'], 'driving_ready': False,
            'nav_map': 'blank', 'single_camera': args.source == 'camera',
            'action_t': [0.2, 0.2], 'started_at': time.time(), 'pid': os.getpid(),
            'transport': 'eonlink-v1' if transport == 'eonlink' else 'tcp-v3', 'timing_version': 3 if transport == 'eonlink' else 2}
  status_at = [0.0]

  def write_status(force=False):
    now = time.monotonic()
    if force or now - status_at[0] >= 0.5:
      status_at[0] = now
      if selection is not None:
        status['model_selection'] = selection.snapshot()
      atomic_json(STATUS, dict(status, updated_at=time.time(), **statistics.snapshot()))

  def check():
    if stopped[0] or STOP.exists():
      raise Stopped('시험 중지')
    if guard:
      guard()
    write_status()

  try:
    write_status(True)
    if guard:
      # The first SubMaster update may not have received its initial samples.
      until = time.monotonic() + 2.0
      while True:
        try:
          check()
          break
        except Stopped:
          if STOP.exists() or stopped[0] or time.monotonic() >= until:
            raise
          time.sleep(0.05)
    host = args.host
    if transport == 'eonlink':
      from selfdrive.modeld.jetlink.eonlink import EonClient, discover
      if host == 'auto':
        host = discover(check)
        status['host'] = host
      sock = socket.create_connection((host, args.port), timeout=3.0)
      client = EonClient(sock, check, compression=getattr(args, 'compression', False))
      status['stage'] = 'link_probe'
      write_status(True)
      status['link_probe'] = {}
      for name, size in (('small', 32), ('image', WARPED_BYTES)):
        probe_data = np.random.RandomState(9208).randint(0, 256, size, dtype=np.uint8).tobytes()
        trials = [client.probe(probe_data) for _ in range(5)]
        status['link_probe'][name + '_mean_ms'] = sum(t['roundtrip_ms'] for t in trials) / len(trials)
        status['link_probe'][name + '_phone_receive_ms'] = sum(t['phone_receive_ms'] for t in trials) / len(trials)
    else:
      sock = socket.create_connection((host, args.port), timeout=3.0)
      client = Client(sock, check)
    status['stage'] = 'preparing'
    write_status(True)
    status['peer'] = client.prepare()
    if args.source == 'camera':
      from selfdrive.modeld.jetlink.selection_monitor import SelectionMonitor
      selection = SelectionMonitor()
      selection.start()
      selection.connect()
    from selfdrive.controls.lib.navigation_route import GUIDE_FILE, NavigationRouteData
    navigation = NavigationRouteData(GUIDE_FILE)
    status['stage'] = 'waiting_frames' if args.source == 'camera' else 'measuring'
    write_status(True)
    started = time.monotonic()
    until = started + args.seconds
    waiting_until = started + 15.0
    next_frame = started
    last_frame = None
    next_demand = 0.0
    synthetic = np.random.RandomState(9208).randint(0, 256, WARPED_BYTES, dtype=np.uint8).tobytes()
    while time.monotonic() < until:
      check()
      now = time.monotonic()
      if args.source == 'camera':
        if now >= next_demand:
          DEMAND.touch()
          next_demand = now + 0.5
        frame = frames.latest(last_frame)
        if frame is None:
          if now >= waiting_until:
            raise LinkError('최신 EON 영상 없음: 새 modeld 빌드·실행과 카메라/캘리브레이션을 확인하세요')
          time.sleep(0.005)
          continue
        waiting_until = now + 3.0
        images, frame_id = frame['images'], frame['frame_id']
        packed = np.array(frame['desire'] + frame['traffic'] + [0.2, 0.2], dtype=np.float32)
        frame_started = frame['started']
        if last_frame is not None:
          gap = (frame_id - last_frame) & 0xffffffff
          statistics.skipped += max(0, gap - 1)
        else:
          gap = 0
      else:
        # Pace at 20 Hz; do not build a queue of synthetic requests when slow.
        if now < next_frame:
          time.sleep(min(0.01, next_frame - now))
          continue
        next_frame = max(next_frame + 0.05, now)
        images = synthetic
        frame_id = statistics.frames + 1
        packed = np.array([0.] * 8 + [1., 0., 0.2, 0.2], dtype=np.float32)
        frame_started, gap = now, 1
      packed = np.concatenate((packed, navigation_input(navigation.update())))
      nav_map, status['nav_map'] = route_map()
      ticket = selection.begin(client.sequence + 1, frame_id, frame_started, bool(frame['flags'] & 2)) if selection is not None else None
      values, timings = client.infer(frame_id, images, packed, nav_map,
                                    reset=last_frame is None or gap != 1)
      converted = prediction.convert(values, frame_id)
      if selection is not None:
        selection.accept(ticket, frame_id, converted)
      status['control_adapter'] = dict(converted=True, applied=False, camera_compatible=False, **converted['summary'])
      if args.source == 'camera':
        candidate = dict(source='camera', frame_id=frame_id, captured_monotonic=frame_started,
                         started_at=status['started_at'], flags=frame['flags'], nav_map=status['nav_map'],
                         applied=False, camera_compatible=False,
                         model_selection=selection.snapshot() if selection is not None else None, **converted)
      statistics.add(timings, (time.monotonic() - frame_started) * 1000)
      last_frame = frame_id
      status.update(stage='measuring', last_frame_id=frame_id, last=timings,
                    output_floats=len(values), plan_y_2s=float(values[917 + 14 * 15 + 1]),
                    elapsed_s=time.monotonic() - started)
      status['response_hz'] = statistics.frames / max(0.001, status['elapsed_s'])
      write_status()
    status['stage'] = 'finished'
  except Stopped as error:
    if selection is not None:
      selection.fail('stopped')
    status.update(stage='stopped', error=str(error))
  except (OSError, ValueError, LinkError, RuntimeError) as error:
    if selection is not None:
      selection.fail('link_error')
    status.update(stage='error', error=str(error)[:500])
  finally:
    if selection is not None:
      selection.close('finished' if status['stage'] == 'finished' else 'stopped' if status['stage'] == 'stopped' else 'link_error')
    if client:
      client.close()
    frames.close()
    DEMAND.unlink(missing_ok=True)
    status['running'] = False
    write_status(True)
    report = dict(status, updated_at=time.time(), **statistics.snapshot())
    atomic_json(args.report, report)
    if candidate is not None:
      atomic_json(Path(args.report).with_suffix('.candidate.json'), candidate)
    lock.close()
  print(json.dumps(report, ensure_ascii=False, allow_nan=False))
  return 0 if status['stage'] == 'finished' else 1


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--host', required=True, type=lambda x: x if x == 'auto' else address(x))
  parser.add_argument('--transport', choices=('jetlink', 'eonlink'), default='jetlink')
  parser.add_argument('--compression', action='store_true', help='EON Link lossless deflate trial')
  parser.add_argument('--port', type=int, default=5599)
  parser.add_argument('--seconds', type=int, default=60, choices=(60, 600))
  parser.add_argument('--source', choices=('synthetic', 'camera'), default='synthetic')
  parser.add_argument('--standalone', action='store_true', help='PC protocol test only, without EON vehicle state')
  parser.add_argument('--report', type=Path, default=REPORT)
  args = parser.parse_args()
  if not 1024 <= args.port <= 65535:
    parser.error('port must be 1024..65535')
  if args.host == 'auto' and args.transport != 'eonlink':
    parser.error('automatic discovery requires EON Link')
  if args.compression and args.transport != 'eonlink':
    parser.error('compression requires EON Link')
  if args.standalone and args.source != 'synthetic':
    parser.error('--standalone supports synthetic input only')
  return run(args)


if __name__ == '__main__':
  sys.exit(main())
