"""Versioned EON Link publication IPC. No network or actuator access here."""
import json
import math
import os
from pathlib import Path
import secrets
import stat
import struct
import time

CONFIG = Path('/data/eonlink-config.json')
CONTROL = Path('/data/eonlink-control')
STATE = Path('/dev/shm/eonlink-native-state')
STATUS = Path('/dev/shm/eonlink-live-status.json')
RESULT = Path('/dev/shm/eonlink-result')
DEMAND = Path('/dev/shm/eonlink-live-demand')
FRAMES = Path('/dev/shm/eonlink-live-frame')
CONTROL_HEADER = struct.Struct('<4sIQQII')  # 32: revision, reserved, enabled, reserved
RESULT_HEADER = struct.Struct('<4sIQQIIQQQII')  # 64
STATE_HEADER = struct.Struct('<4sIQIIIIQIIQQ')  # 64
MAX_RESULT = 131072
NATIVE_OK, ENGAGED, CALIBRATED, EXTERNAL, LATCHED, CAN_OK = 1, 2, 4, 8, 16, 32


def atomic(path, data):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  temp = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
  fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
  try:
    with os.fdopen(fd, 'wb') as target:
      target.write(data)
    os.replace(str(temp), str(path))
  finally:
    try: temp.unlink()
    except FileNotFoundError: pass


def read(path, maximum):
  fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
  with os.fdopen(fd, 'rb') as source:
    info = os.fstat(source.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
      raise ValueError('invalid EON Link IPC file')
    value = source.read(maximum + 1)
  if len(value) > maximum:
    raise ValueError('oversized EON Link IPC file')
  return value


def profile(path=None):
  path = CONFIG if path is None else path
  default = dict(mode='native', host='auto', port=5600, compression=False, revision='0')
  try:
    value = json.loads(read(path, 4096))
    if not isinstance(value, dict) or value.get('mode') not in ('native', 'external'):
      return default
    import ipaddress
    host = value.get('host')
    if host != 'auto':
      address = ipaddress.ip_address(host)
      if address.version != 4 or not address.is_private or address.is_loopback or address.is_unspecified or address.is_multicast:
        return default
    if type(value.get('port')) is not int or not 1024 <= value['port'] <= 65535 or type(value.get('compression')) is not bool:
      return default
    revision = int(value['revision'])
    if not 0 < revision < 2 ** 64:
      return default
    return dict(mode=value['mode'], host=host, port=value['port'], compression=value['compression'], revision=str(revision))
  except (OSError, ValueError, KeyError, TypeError):
    return default


def control(path=None):
  path = CONTROL if path is None else path
  try:
    magic, version, revision, reserved, enabled, tail = CONTROL_HEADER.unpack(read(path, CONTROL_HEADER.size))
    if magic == b'ELC1' and version == 1 and revision and not reserved and not tail and enabled in (0, 1):
      return revision, bool(enabled)
  except (OSError, ValueError, struct.error):
    pass
  return 0, False


def save_profile(value, config_path=None, control_path=None):
  config_path = CONFIG if config_path is None else config_path
  control_path = CONTROL if control_path is None else control_path
  revision = secrets.randbits(64) or 1
  value = dict(value, revision=str(revision))
  # Disarm first. A worker racing this save can only write the old revision.
  atomic(control_path, CONTROL_HEADER.pack(b'ELC1', 1, revision, 0, 0, 0))
  atomic(config_path, json.dumps(value, allow_nan=False).encode())
  if value['mode'] == 'external':
    atomic(control_path, CONTROL_HEADER.pack(b'ELC1', 1, revision, 0, 1, 0))
  return value


def native_state(path=None, now=None):
  path = STATE if path is None else path
  try:
    values = STATE_HEADER.unpack(read(path, STATE_HEADER.size))
    # The producer may publish between entering this function and read().
    # Sample after the snapshot, otherwise a brand-new state looks future-dated.
    now = time.monotonic() if now is None else now
    magic, version, stamp, frame, flags, selected_frame, sequence, session, reason, reserved, finished, revision = values
    age = now - stamp / 1e9
    if magic != b'ELS1' or version != 1 or reserved or flags & ~63 or not 0 <= age < .25:
      return None
    healthy = bool(flags & NATIVE_OK) and 0 <= now - finished / 1e9 < .15
    return dict(frame_id=frame, flags=flags, native_available=healthy, engaged=bool(flags & ENGAGED),
                calibrated=bool(flags & CALIBRATED), can_valid=bool(flags & CAN_OK),
                applied=bool(flags & EXTERNAL), latched=bool(flags & LATCHED),
                selected_frame=selected_frame, sequence=sequence, session=session, reason=reason,
                revision=str(revision), age_ms=age * 1000)
  except (OSError, ValueError, struct.error):
    return None


def encode_result(revision, session, sequence, frame, started, completed, camera_ns, payload):
  if not isinstance(payload, bytes) or not payload or len(payload) > MAX_RESULT or len(payload) % 8:
    raise ValueError('invalid publication payload size')
  if not all(math.isfinite(t) and t > 0 for t in (started, completed)) or not started <= completed <= started + .05:
    raise ValueError('late EON Link publication')
  return RESULT_HEADER.pack(b'ELR1', 1, revision, session, sequence, frame,
                            int(started * 1e9), int(completed * 1e9), camera_ns, len(payload), 1) + payload


class FallbackMonitor:
  """Throttle controlsd IPC reads; alert on modeld's actual applied-source loss."""
  def __init__(self):
    self.next_read = 0.
    self.failed = False

  def update(self, now=None):
    now = time.monotonic() if now is None else now
    if now < self.next_read:
      return self.failed
    self.next_read = now + .1
    revision, enabled = control()
    state = native_state(now=now)
    self.failed = bool(enabled and state and state['revision'] == str(revision) and state['latched'])
    return self.failed
