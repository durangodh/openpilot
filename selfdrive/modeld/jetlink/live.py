"""EON Link live model worker. modeld alone owns authoritative publication.

The worker never publishes CAN/carControl or model topics. It supplies a checked
candidate; modeld validates it again and independently enforces fallback.
"""
import contextlib
import fcntl
import json
import math
import signal
import socket
import time
from pathlib import Path

import numpy as np

from selfdrive.modeld.jetlink import live_ipc as ipc
from selfdrive.modeld.jetlink.client import LinkError, navigation_input
from selfdrive.modeld.jetlink.eonlink import EonClient, discover
from selfdrive.modeld.jetlink.frames import Frames, route_map
from selfdrive.modeld.jetlink.prediction import PredictionState
from selfdrive.modeld.jetlink.selection import ModelSelector

LOCK = Path('/dev/shm/eon-n16-lock')


class Changed(RuntimeError):
  pass


def actuator_times():
  from common.params import Params
  from cereal import car
  raw = Params().get('CarParams')
  if not raw:
    raise LinkError('차량 설정 대기 중')
  parsed = car.CarParams.from_bytes(raw)
  context = parsed if hasattr(parsed, '__enter__') else contextlib.nullcontext(parsed)
  with context as cp:
    lateral = float(cp.steerActuatorDelay)
    longitudinal = float(cp.longitudinalActuatorDelayUpperBound)
  if not all(math.isfinite(t) and 0 < t <= 2 for t in (lateral, longitudinal)):
    raise LinkError('차량 조향·가감속 지연 설정을 확인하세요')
  return lateral, longitudinal


def model_event(converted, frame):
  from cereal import log
  clock = getattr(time, 'CLOCK_BOOTTIME', time.CLOCK_MONOTONIC)
  message = log.Event.new_message(valid=True, logMonoTime=time.clock_gettime_ns(clock))
  message.init('modelV2')
  message.modelV2 = dict(converted['model'], frameId=frame['frame_id'], frameIdExtra=frame['frame_id'],
                        frameAge=0, frameDropPerc=0., timestampEof=frame['camera_timestamp_ns'], modelExecutionTime=0.)
  return message.to_bytes()


def clear_results():
  for path in (ipc.RESULT, ipc.DEMAND):
    try: path.unlink()
    except FileNotFoundError: pass


class LiveWorker:
  def __init__(self):
    self.stopped = False
    self.last_status = 0.
    self.policy = None
    self.info = {}

  def status(self, force=False):
    now = time.monotonic()
    if not force and now - self.last_status < .5:
      return
    self.last_status = now
    state = ipc.native_state()
    applied = bool(state and state['revision'] == self.info.get('revision') and state['applied'])
    value = dict(self.info, updated_at=time.time(), applied=applied,
                 actual_source='external' if applied else 'native', native=state,
                 experimental=True, camera_compatible=False)
    ipc.atomic(ipc.STATUS, json.dumps(value, ensure_ascii=False, allow_nan=False).encode())

  def check(self):
    if self.stopped or ipc.control() != (int(self.info['revision']), True):
      raise Changed('모델 선택 변경 또는 종료')
    state = ipc.native_state()
    if not state:
      raise LinkError('modeld 상태 파일 미수신·만료: 재시작·모델 처리 지연 확인')
    if not state['native_available']:
      raise LinkError('기존 모델 정상 출력·처리 시각 확인 대기')
    if not state['can_valid']:
      raise LinkError('modeld의 차량 CAN·controlsState 수신 확인 대기')
    if not state['calibrated']:
      raise LinkError('modeld의 카메라 보정 완료·수신 확인 대기')
    self.status()
    return state

  def session(self, config):
    lock = open(LOCK, 'a')
    try:
      fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
      lock.close()
      raise LinkError('다른 N16 연결·시험이 실행 중입니다')
    client = None
    frames = Frames(ipc.FRAMES)
    self.policy = ModelSelector()
    # This explicitly named experimental mode is not a claim of camera or
    # real-vehicle validation. Default/benchmark selectors remain unarmed.
    self.policy.configure(enabled=True, experimental=True)
    try:
      clear_results()
      self.info = dict(phase='connecting', revision=config['revision'], host=config['host'], port=config['port'],
                       frames=0, error='', nav_map='blank')
      self.status(True)
      lateral, longitudinal = actuator_times()
      self.check()
      host = discover(self.check) if config['host'] == 'auto' else config['host']
      self.info['host'] = host
      client = EonClient(socket.create_connection((host, config['port']), timeout=3), self.check, config['compression'])
      self.info['peer'] = client.prepare()
      session = int(self.policy.connect()[:16], 16) or 1
      from selfdrive.controls.lib.navigation_route import GUIDE_FILE, NavigationRouteData
      navigation = NavigationRouteData(GUIDE_FILE)
      prediction = PredictionState()
      last_read = last_sent = None
      previous_latch = False
      self.info.update(phase='waiting_camera', action_t=[lateral, longitudinal])
      next_demand, waiting_until = 0., time.monotonic() + 15
      while not self.stopped:
        native = self.check()
        if native['latched'] and not previous_latch:
          self.policy._drop('publication_fallback')
          self.policy.latch = True
        previous_latch = native['latched']
        now = time.monotonic()
        if now >= next_demand:
          ipc.DEMAND.touch(); next_demand = now + .5
        frame = frames.latest(last_read)
        if frame is None:
          if not self.info['frames']:
            self.info['error'] = ' · '.join(filter(None, (ipc.camera_reason(native.get('reason', 0)), getattr(frames, 'reason', '영상 입력 대기'))))
          decision = self.policy.decide(native['frame_id'], native['native_available'], native['engaged'])
          self.info['selection'] = self.policy.snapshot(decision)
          if decision.source != 'external':
            try: ipc.RESULT.unlink()
            except FileNotFoundError: pass
          if now > waiting_until:
            raise LinkError('실제 카메라 입력 없음: ' + self.info.get('error', 'modeld 재빌드·보정 상태 확인'))
          time.sleep(.005)
          continue
        waiting_until = now + 3
        last_read = frame['frame_id']
        ticket = self.policy.begin(client.sequence + 1, frame['frame_id'], frame['started'], True, bool(frame['flags'] & 2))
        if ticket is None:
          self.info['error'] = self.policy.reason
          try: ipc.RESULT.unlink()
          except FileNotFoundError: pass
          continue
        packed = np.array(frame['desire'] + frame['traffic'] + [lateral, longitudinal], dtype=np.float32)
        packed = np.concatenate((packed, navigation_input(navigation.update())))
        nav_map, self.info['nav_map'] = route_map()
        reset = last_sent is None or ((frame['frame_id'] - last_sent) & 0xffffffff) != 1
        values, timing = client.infer(frame['frame_id'], frame['images'], packed, nav_map, reset=reset)
        last_sent = frame['frame_id']
        converted = prediction.convert(values, frame['frame_id'])
        payload = model_event(converted, frame)
        accepted = self.policy.accept(ticket, frame['frame_id'], converted)
        native = self.check()
        decision = self.policy.decide(native['frame_id'], native['native_available'], native['engaged'])
        self.info.update(selection=self.policy.snapshot(decision), frames=self.info['frames'] + 1,
                         phase='external_candidate' if decision.source == 'external' else 'native',
                         error='' if accepted else self.policy.reason, last=timing, **converted['summary'])
        if decision.source == 'external' and decision.ticket == ticket:
          completed = time.monotonic()
          try:
            data = ipc.encode_result(int(config['revision']), session, ticket.sequence, frame['frame_id'],
                                     frame['started'], completed, frame['camera_timestamp_ns'], payload)
          except ValueError:
            self.policy._drop('late_response')
            try: ipc.RESULT.unlink()
            except FileNotFoundError: pass
          else:
            if ipc.control() == (int(config['revision']), True):
              ipc.atomic(ipc.RESULT, data)
        else:
          try: ipc.RESULT.unlink()
          except FileNotFoundError: pass
        self.status()
    finally:
      self.policy.disconnect('disconnected')
      clear_results()
      if client is not None:
        client.close()
      frames.close()
      lock.close()

  def run(self):
    delay = .5
    while not self.stopped:
      config = ipc.profile()
      if config['mode'] != 'external' or ipc.control() != (int(config['revision']), True):
        self.info = dict(phase='native', revision=config['revision'], error='')
        clear_results(); self.status()
        time.sleep(.1)
        delay = .5
        continue
      try:
        self.session(config)
      except Changed:
        delay = .5
      except (OSError, ValueError, RuntimeError, LinkError) as error:
        self.info.update(phase='retrying', error=str(error)[:250])
        if self.policy is not None:
          self.info['selection'] = self.policy.snapshot(self.policy.decide(0, False, True))
        self.status(True)
        deadline = time.monotonic() + delay
        while not self.stopped and time.monotonic() < deadline and ipc.control() == (int(config['revision']), True):
          self.status()  # retain the retry reason instead of appearing dead after 3s
          time.sleep(.05)
        delay = min(5., delay * 2)
    clear_results()
    self.info.update(phase='stopped'); self.status(True)


def main():
  worker = LiveWorker()
  signal.signal(signal.SIGTERM, lambda *_: setattr(worker, 'stopped', True))
  signal.signal(signal.SIGINT, lambda *_: setattr(worker, 'stopped', True))
  worker.run()


if __name__ == '__main__':
  main()
