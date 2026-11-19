"""Read modeld's optional diagnostic snapshot without blocking its writer."""
import fcntl
import math
import mmap
import os
import stat
import struct
import time
from pathlib import Path

from selfdrive.modeld.jetlink.client import MAP_BYTES, WARPED_BYTES

DEMAND = Path('/dev/shm/eon-n16-demand')
FRAMES = Path('/dev/shm/eon-n16-frame')
FRAME_HEADER = struct.Struct('<4sIQQII8f2fQ16x')
FRAME_SIZE = FRAME_HEADER.size + WARPED_BYTES
MAP_HEADER = struct.Struct('<4sQQB')


class Frames:
  def __init__(self, path=FRAMES):
    self.path = Path(path)
    self.file = None
    self.mapping = None

  def close(self):
    if self.mapping is not None:
      self.mapping.close()
    if self.file is not None:
      self.file.close()
    self.file = self.mapping = None

  def latest(self, last=None, now=None):
    now = time.monotonic() if now is None else now
    try:
      if self.file is None:
        fd = os.open(str(self.path), os.O_RDONLY | os.O_NOFOLLOW)
        self.file = os.fdopen(fd, 'rb')
        st = os.fstat(fd)
        if st.st_size != FRAME_SIZE or not stat.S_ISREG(st.st_mode):
          self.close()
          return None
        self.mapping = mmap.mmap(fd, FRAME_SIZE, access=mmap.ACCESS_READ)
      fcntl.flock(self.file.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
      try:
        data = self.mapping[:]
      finally:
        fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
      magic, version, generation, started_ns, frame_id, flags, *rest = FRAME_HEADER.unpack_from(data)
      if magic != b'JLE1' or version != 1 or not generation or generation & 1 or frame_id == last:
        return None
      age = now - started_ns / 1e9
      if not 0 <= age < 0.15 or flags & ~3 or not flags & 2 or not all(math.isfinite(x) for x in rest[:10]):
        return None
      return {'frame_id': frame_id, 'started': started_ns / 1e9, 'flags': flags,
              'desire': rest[:8], 'traffic': rest[8:10], 'camera_timestamp_ns': rest[10], 'images': data[FRAME_HEADER.size:]}
    except BlockingIOError:
      return None
    except (OSError, ValueError):
      self.close()
      return None


def route_map(path=Path('/dev/shm/carrot-nav-map'), now=None):
  now = time.monotonic() if now is None else now
  try:
    with open(path, 'rb') as source:
      data = source.read(MAP_HEADER.size + MAP_BYTES + 1)
    if len(data) == MAP_HEADER.size + MAP_BYTES:
      magic, _, rendered_ns, valid = MAP_HEADER.unpack_from(data)
      if magic == b'NMAP' and valid == 1 and 0 <= now - rendered_ns / 1e9 < 0.5:
        return data[MAP_HEADER.size:], 'route'
  except OSError:
    pass
  # EON currently has no N16 tile renderer. A blank map is the original
  # protocol's no-route input, not a fabricated route or driving validation.
  return bytes(MAP_BYTES), 'blank'
