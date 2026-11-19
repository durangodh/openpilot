"""Small, deadline-bounded Jetlink v3 client for the EON performance probe.

Wire ABI: bin9208/jetlink carrot-noo 1133658a, JetlinkServer/WireProtocol.swift.
N16 contract: ajouatom/openpilot carrot-rust-dev c18326a46. No control publisher.
"""
import json
import math
import socket
import struct
import time
from pathlib import Path

import numpy as np

HEADER = struct.Struct('<4sHHIIIQ4x')
INFER = struct.Struct('<IIIII')
MAGIC, VERSION = b'JLNK', 3
PADDED, NAV_MAP, RESET = 128, 8, 1
WARPED_BYTES, MAP_BYTES = 2 * 6 * 128 * 256, 256 * 256
SPEC = json.loads(Path(__file__).with_name('n16.json').read_text())


class LinkError(RuntimeError):
  pass


class Client:
  def __init__(self, sock, check=lambda: None):
    self.sock = sock
    self.check = check
    self.sequence = 0
    self.peer = {}
    self.last_map = None
    self.check_seconds = 0.0
    self.last_tx_bytes = 0
    self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

  def close(self):
    self.sock.close()

  def _timeout(self, deadline):
    started = time.monotonic()
    try:
      self.check()
    finally:
      self.check_seconds += time.monotonic() - started
    remaining = deadline - time.monotonic()
    if remaining <= 0:
      raise LinkError('Jetlink 응답 시간 초과')
    self.sock.settimeout(min(remaining, 0.2))

  def _send(self, kind, payload, deadline):
    return self._send_parts(kind, (payload,), deadline)

  def _send_parts(self, kind, parts, deadline):
    self.sequence += 1
    if self.sequence >= 0xffffffff:
      raise LinkError('sequence exhausted')
    # Keep the image borrowed. Building header+images+scalars+map used to
    # copy the entire ~450 KiB request several times on the EON CPU.
    views = [memoryview(part) for part in parts if len(part)]
    size = sum(view.nbytes for view in views)
    padded = (HEADER.size + size) % 512 == 0
    header = HEADER.pack(MAGIC, VERSION, kind, self.sequence, PADDED if padded else 0, size, 0)
    views.insert(0, memoryview(header))
    if padded:
      views.append(memoryview(b'\0'))
    self.last_tx_bytes = HEADER.size + size + int(padded)
    sendmsg = getattr(self.sock, 'sendmsg', None)
    if not callable(sendmsg):
      views = [memoryview(b''.join(views))]
    while views:
      self._timeout(deadline)
      try:
        sent = sendmsg(views) if callable(sendmsg) else self.sock.send(views[0])
      except socket.timeout:
        continue
      if not sent:
        raise LinkError('Jetlink 연결 종료')
      while views and sent >= len(views[0]):
        sent -= len(views.pop(0))
      if views and sent:
        views[0] = views[0][sent:]
    return self.sequence

  def _read(self, size, deadline):
    result = bytearray(size)
    view = memoryview(result)
    while view:
      self._timeout(deadline)
      try:
        count = self.sock.recv_into(view)
      except socket.timeout:
        continue
      if not count:
        raise LinkError('Jetlink 연결 종료')
      view = view[count:]
    return result

  def _receive(self, deadline):
    magic, version, kind, sequence, flags, size, reserved = HEADER.unpack(self._read(32, deadline))
    if magic != MAGIC or version != VERSION or reserved != 0 or flags & ~PADDED or size > 2 * 1024 * 1024:
      raise LinkError('Jetlink 헤더/프로토콜 오류')
    payload = self._read(size, deadline)
    if flags & PADDED:
      self._read(1, deadline)
    if kind == 14:
      raise LinkError('Jetlink 서버 오류: ' + bytes(payload[:400]).decode('utf-8', errors='replace'))
    return kind, sequence, payload

  def _expect(self, kind, sequence, deadline):
    for _ in range(128):
      actual, seq, payload = self._receive(deadline)
      if actual == kind and seq == sequence:
        return payload
      # Progress and state notifications may be interleaved. An unrelated
      # inference response must never acquire this request's identity.
      if actual not in (4, 7, 13):
        raise LinkError('Jetlink 응답 순서 불일치')
    raise LinkError('Jetlink 알림 과다')

  def prepare(self, timeout=60.0):
    deadline = time.monotonic() + timeout
    seq = self._send(1, b'{"client":{"name":"eon-n16-benchmark"}}', deadline)
    self.peer = json.loads(self._expect(2, seq, deadline))
    if not isinstance(self.peer, dict) or self.peer.get('protocol') != VERSION:
      raise LinkError('Jetlink v3 앱이 필요합니다')
    # The phone owns download/compile. Never silently change or upload a model.
    if self.peer.get('loaded') != SPEC['sha256']:
      raise LinkError('S20 Jetlink 앱에서 N16을 선택하고 준비를 완료하세요')
    request = {key: SPEC[key] for key in ('sha256', 'nbytes', 'frame_skip')}
    seq = self._send(3, json.dumps(request).encode(), deadline)
    state = json.loads(self._expect(4, seq, deadline))
    if not isinstance(state, dict):
      raise LinkError('N16 엔진 응답 형식 오류')
    while state.get('state') != 'ready':
      if state.get('state') not in ('building', 'loading', 'preparing'):
        raise LinkError('N16 엔진 준비 실패: ' + str(state.get('state'))[:80])
      kind, _, payload = self._receive(deadline)
      if kind == 4:
        state = json.loads(payload)
        if not isinstance(state, dict):
          raise LinkError('N16 엔진 응답 형식 오류')
      elif kind not in (7, 13):
        raise LinkError('N16 준비 중 잘못된 응답')
    spec = state.get('spec')
    if not isinstance(spec, dict) or any(spec.get(key) != SPEC[key] for key in
                                        ('sha256', 'nbytes', 'frame_skip', 'input_shapes', 'output_shapes', 'output_slices')):
      raise LinkError('N16 모델 입출력 규격이 다릅니다')
    self.last_map = None
    return {key: self.peer.get(key) for key in ('device', 'backend', 'runtime_version')}

  def infer(self, frame_id, images, packed, nav_map=None, reset=False, timeout=3.0):
    packed = np.asarray(packed, dtype='<f4')
    if len(images) != WARPED_BYTES or packed.shape != (59,) or not np.isfinite(packed).all():
      raise LinkError('N16 입력 크기/수치 오류')
    if nav_map is not None and len(nav_map) != MAP_BYTES:
      raise LinkError('N16 지도 크기 오류')
    started = time.monotonic()
    check_before = self.check_seconds
    deadline = started + timeout
    if nav_map is not None:
      nav_map = bytes(nav_map)
    # The server retains its last map until reset. Re-send on a change or
    # reset only, including one black map when a route becomes unavailable.
    with_map = nav_map is not None and (reset or nav_map != self.last_map)
    flags = (RESET if reset else 0) | (NAV_MAP if with_map else 0)
    parts = (struct.pack('<II', frame_id, flags), images, packed.tobytes())
    if with_map:
      parts += (nav_map,)
    prepared = time.monotonic()
    seq = self._send_parts(8, parts, deadline)
    sent_at = time.monotonic()
    payload = self._expect(9, seq, deadline)
    received_at = time.monotonic()
    if len(payload) < INFER.size:
      raise LinkError('N16 출력 헤더 누락')
    answered, status, gpu, queue, total = INFER.unpack_from(payload)
    if answered != frame_id or status != 0:
      raise LinkError('N16 출력 프레임/상태 오류: %s/%s' % (answered, status))
    start, stop = SPEC['output_slices']['hidden_state']
    count = SPEC['output_shapes']['outputs'][1] - (stop - start)
    if len(payload) != INFER.size + count * 4:
      raise LinkError('N16 출력 길이 불일치')
    values = np.frombuffer(payload, dtype='<f4', offset=INFER.size)
    if not np.isfinite(values).all():
      raise LinkError('N16 출력 NaN/무한대')
    full = np.zeros(SPEC['output_shapes']['outputs'][1], dtype=np.float32)
    full[:start], full[stop:] = values[:start], values[start:]
    self.last_map = nav_map if with_map else None if reset else self.last_map
    done = time.monotonic()
    return full, {'roundtrip_ms': (done - started) * 1000,
                  'pack_ms': (prepared - started) * 1000, 'send_ms': (sent_at - prepared) * 1000,
                  'wait_ms': (received_at - sent_at) * 1000, 'decode_ms': (done - received_at) * 1000,
                  'check_ms': (self.check_seconds - check_before) * 1000,
                  'tx_bytes': self.last_tx_bytes, 'map_sent': with_map,
                  'gpu_ms': gpu / 1000.0, 'queue_ms': queue / 1000.0, 'server_ms': total / 1000.0}


def navigation_input(state):
  """N16's Valhalla command one-hot + distance/100, current guidance only."""
  result = np.zeros(47, dtype=np.float32)
  if not state.get('fresh') or state.get('off_route'):
    return result
  distance = state.get('distance', -1.0)
  if not isinstance(distance, (float, int)) or not math.isfinite(distance) or distance < 0:
    return result
  kind, direction = state.get('kind'), state.get('direction')
  if kind == 'uturn':
    command = 13
  elif kind == 'rotary':
    command = 26
  elif kind == 'fork' and direction in (-1, 1):
    command = 24 if direction < 0 else 23
  elif kind == 'turn' and direction in (-1, 1):
    command = (14 if direction < 0 else 11) if state.get('sharp_turn') else (15 if direction < 0 else 10)
  else:
    return result
  result[command], result[46] = 1.0, distance / 100.0
  return result
