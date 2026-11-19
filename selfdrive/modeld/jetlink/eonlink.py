"""EON Link v1 lossless envelope over Jetlink v3.

No frame queue, no lossy image conversion, no model/control publishers.
"""
import hashlib
import ipaddress
import json
import os
import socket
import struct
import time
import zlib

from selfdrive.modeld.jetlink.client import Client, LinkError, HEADER, MAGIC, VERSION, PADDED, SPEC

ENVELOPE = struct.Struct('<4sBBHIIIIII')
LIMIT, DEFLATE, ECHO = 1048576, 1, 2
DISCOVERY = b'EONLINK_DISCOVER_V1'


def interface_names():
  # Android/Termux Python 3.8 may omit if_nameindex entirely. Prefer the
  # native API when present, then use Linux interface names without guessing
  # a hotspot subnet or hard-coding wlan0.
  enumerate_names = getattr(socket, 'if_nameindex', None)
  if enumerate_names is not None:
    try:
      return [name for _, name in enumerate_names()]
    except (OSError, NotImplementedError):
      pass
  try:
    names = os.listdir('/sys/class/net')
    if names:
      return sorted(names)
  except OSError:
    pass
  try:
    with open('/proc/net/dev') as source:
      return sorted({line.split(':', 1)[0].strip() for line in source if ':' in line})
  except OSError:
    # Global broadcast remains available even if Android restricts both files.
    return []


def broadcasts():
  # Query actual interface broadcasts: hotspots need not use a /24 subnet.
  import fcntl
  targets = {'255.255.255.255'}
  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    for name in interface_names():
      try:
        result = fcntl.ioctl(sock.fileno(), 0x8919, struct.pack('256s', name[:15].encode()))
        targets.add(socket.inet_ntoa(result[20:24]))
      except OSError:
        continue
  return sorted(targets - {'0.0.0.0'})


def discover(check=lambda: None, timeout=2.0):
  found = set()
  deadline = time.monotonic() + timeout
  with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.1)
    targets = broadcasts()
    for _ in range(2):
      for target in targets:
        try:
          sock.sendto(DISCOVERY, (target, 5601))
        except OSError:
          pass
    while time.monotonic() < deadline:
      check()
      try:
        raw, peer = sock.recvfrom(1024)
        value = json.loads(raw)
        ip = ipaddress.ip_address(peer[0])
        if ip.is_private and not ip.is_loopback and value == dict(app='eonlink', version=1, port=5600, model=SPEC['sha256']):
          found.add(peer[0])
      except (OSError, ValueError, TypeError):
        continue
  if not found:
    raise LinkError('이온링크 자동 검색 실패: 앱의 Wi-Fi IP를 직접 입력하세요')
  if len(found) != 1:
    raise LinkError('이온링크가 여러 대입니다. IP를 선택하세요: ' + ', '.join(sorted(found)))
  return found.pop()


class EonClient(Client):
  def __init__(self, sock, check=lambda: None, compression=False):
    super().__init__(sock, check)
    self.compression = compression
    self.phone = {}
    self.plain_bytes = 0
    self.compress_ms = 0.0

  def _packet(self, sequence, parts, flags, deadline):
    self.plain_bytes = sum(len(part) for part in parts)
    if self.plain_bytes > LIMIT:
      raise LinkError('EON Link request size')
    started = time.monotonic()
    if self.compression and self.plain_bytes >= 65536:
      raw = b''.join(parts)
      compressed = zlib.compress(raw, 1)
      if len(compressed) < len(raw) * 0.95:
        parts, flags = (compressed,), flags | DEFLATE
    self.compress_ms = (time.monotonic() - started) * 1000
    size = sum(len(part) for part in parts)
    head = ENVELOPE.pack(b'EON1', 1, flags, 0, sequence, self.plain_bytes, size, 0, 0, 0)
    self.last_tx_bytes = ENVELOPE.size + size
    self._write_parts([memoryview(head)] + [memoryview(p) for p in parts], deadline)

  def _send_parts(self, kind, parts, deadline):
    self.sequence += 1
    if self.sequence >= 0xffffffff:
      raise LinkError('sequence exhausted')
    size = sum(len(part) for part in parts)
    padded = (HEADER.size + size) % 512 == 0
    head = HEADER.pack(MAGIC, VERSION, kind, self.sequence, PADDED if padded else 0, size, 0)
    self._packet(self.sequence, (head,) + tuple(parts) + ((b'\0',) if padded else ()), 0, deadline)
    return self.sequence

  def _envelope(self, deadline):
    magic, version, flags, reserved, seq, plain, size, rx, inflate, engine = ENVELOPE.unpack(self._read(ENVELOPE.size, deadline))
    if magic != b'EON1' or version != 1 or reserved or flags not in (0, ECHO) or plain != size or size > LIMIT:
      raise LinkError('EON Link response header')
    payload = self._read(size, deadline)
    self.phone = dict(phone_receive_ms=rx / 1000.0, phone_inflate_ms=inflate / 1000.0, phone_engine_ms=engine / 1000.0)
    return seq, flags, payload

  def _receive(self, deadline):
    outer_seq, flags, raw = self._envelope(deadline)
    if flags or len(raw) < HEADER.size:
      raise LinkError('EON Link response framing')
    magic, version, kind, seq, flags, size, reserved = HEADER.unpack_from(raw)
    if magic != MAGIC or version != VERSION or reserved or flags & ~PADDED or seq != outer_seq or len(raw) != HEADER.size + size + bool(flags & PADDED):
      raise LinkError('Jetlink header inside EON Link')
    payload = raw[HEADER.size:HEADER.size + size]
    if kind == 14:
      raise LinkError('Jetlink 서버 오류: ' + bytes(payload[:400]).decode('utf-8', errors='replace'))
    return kind, seq, payload

  def infer(self, *args, **kwargs):
    values, timings = super().infer(*args, **kwargs)
    timings.update(self.phone, plain_tx_bytes=self.plain_bytes + ENVELOPE.size, compress_ms=self.compress_ms)
    return values, timings

  def probe(self, data, timeout=3.0):
    start = time.monotonic()
    self.sequence += 1
    deadline = start + timeout
    self._packet(self.sequence, (data,), ECHO, deadline)
    seq, flags, reply = self._envelope(deadline)
    if flags != ECHO or seq != self.sequence or reply != hashlib.sha256(data).digest():
      raise LinkError('EON Link echo mismatch')
    return dict(roundtrip_ms=(time.monotonic() - start) * 1000, **self.phone)
