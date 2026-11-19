"""On-demand road camera and bounded EON route replay."""

import bz2
import io
import math
import json
import os
import stat
import struct
from pathlib import Path
import re
import subprocess
import time
from contextlib import ExitStack

VIDEO_FILE = "qcamera.mp4"
FRAME_INTERVAL = 1 / 3
SCREEN_FRAME_INTERVAL = 1.0
MAX_LOG_BYTES = 32 * 1024 * 1024
MAX_REPLAY_SECONDS = 120


class RoadPreview:
  def __init__(self):
    self.client = None
    self.image = None
    self.updated = 0
    self.requested = 0

  def expire(self):
    if self.client is not None and time.monotonic() - self.requested > 3:
      self.client = None
      self.image = None

  def frame(self):
    from PIL import Image
    from cereal.visionipc.visionipc_pyx import VisionIpcClient, VisionStreamType

    now = time.monotonic()
    self.requested = now
    if now - self.updated < FRAME_INTERVAL:
      return self.image
    self.updated = now
    self.image = None
    if self.client is None:
      client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_RGB_ROAD, True)
      if not client.connect(False):
        return None
      self.client = client
    data = self.client.recv(100)
    if data is None:
      self.client = None
      return None
    width, height, stride = self.client.width, self.client.height, self.client.stride
    image = Image.frombytes("RGB", (width, height), data.tobytes(), "raw", "BGR", stride)
    image.thumbnail((480, 360))
    output = io.BytesIO()
    image.save(output, "JPEG", quality=55)
    self.image = output.getvalue()
    return self.image


SCREEN_HEADER = struct.Struct('<4sIQQIIII')
MAX_SCREEN_BYTES = 2 * 1024 * 1024


class ScreenPreview:
  """Demand-only Qt UI snapshots; no Android compositor subprocess."""
  def __init__(self, root=Path('/dev/shm'), clock=time.monotonic):
    self.root = Path(root)
    self.clock = clock
    self.image = None
    self.content_type = 'image/jpeg'
    self.captured = 0
    self.next_capture = 0
    self.error = '이온 UI 화면 요청 중'

  def _request(self):
    fd = os.open(str(self.root / 'eon-web-screen.request'),
                 os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
      if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise ValueError('invalid screen request file')
      os.write(fd, str(int(self.clock() * 1e9)).encode('ascii'))
    finally:
      os.close(fd)

  def _capture_error(self):
    names = {'ui_hidden': '이온 UI 창이 표시되지 않아 캡처할 수 없습니다',
             'grab_failed': '이온 UI 화면 읽기에 실패했습니다',
             'encode_failed': '이온 UI 이미지 변환에 실패했습니다',
             'write_failed': '이온 UI 캡처 파일 저장에 실패했습니다'}
    try:
      with open(self.root / 'eon-web-screen.error', 'rb') as source:
        data = json.loads(source.read(513))
      age = self.clock() - int(data['at_ns']) / 1e9
      if 0 <= age < 3:
        return names.get(data['error'], '이온 UI 캡처에 실패했습니다')
    except (OSError, ValueError, KeyError, TypeError):
      pass
    return '이온 UI 캡처 응답 대기 중 · 업데이트 후 UI 재빌드·재시작을 확인하세요'

  def frame(self):
    try:
      self._request()
    except (OSError, ValueError):
      self.image = None
      self.error = '이온 UI 화면 요청 파일을 만들지 못했습니다'
      return None
    now = self.clock()
    if now < self.next_capture and self.image is not None and 0 <= now - self.captured < 3:
      return self.image
    self.next_capture = now + SCREEN_FRAME_INTERVAL
    self.image = None
    try:
      fd = os.open(str(self.root / 'eon-web-screen.frame'), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
      with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not SCREEN_HEADER.size < info.st_size <= MAX_SCREEN_BYTES + SCREEN_HEADER.size:
          raise ValueError('invalid Qt snapshot file')
        data = source.read(MAX_SCREEN_BYTES + SCREEN_HEADER.size + 1)
      magic, version, captured, finished, width, height, format_id, size = SCREEN_HEADER.unpack_from(data)
      now_ns = int(self.clock() * 1e9)
      image = data[SCREEN_HEADER.size:]
      if magic != b'EWS1' or version != 1 or not 0 < width <= 960 or not 0 < height <= 540 or size != len(image):
        raise ValueError('invalid Qt snapshot header')
      if not 0 < captured <= finished <= now_ns or now_ns - captured >= 3_000_000_000:
        self.error = self._capture_error()
        return None
      if format_id == 1 and image.startswith(b'\xff\xd8') and image.endswith(b'\xff\xd9'):
        self.content_type = 'image/jpeg'
      elif format_id == 2 and image.startswith(b'\x89PNG\r\n\x1a\n'):
        self.content_type = 'image/png'
      else:
        raise ValueError('invalid Qt snapshot image')
      self.captured = captured / 1e9
      self.image = image
      self.error = ''
    except FileNotFoundError:
      self.error = self._capture_error()
    except (OSError, ValueError, struct.error):
      self.error = '이온 UI 캡처 파일을 읽지 못했습니다 · UI 재빌드·재시작을 확인하세요'
    return self.image


def media_range(header, size):
  if not header:
    return 0, size
  match = re.fullmatch(r"bytes=(\d{0,20})-(\d{0,20})", header)
  if not match or not size:
    raise ValueError("invalid range")
  first, last = match.groups()
  if not first:
    if not last or not int(last):
      raise ValueError("invalid suffix")
    return max(0, size - int(last)), size
  start = int(first)
  end = min(size, int(last) + 1) if last else size
  if start >= end:
    raise ValueError("invalid range")
  return start, end


def finite(value, scale=1):
  return round(value * scale, 3) if math.isfinite(value) else None


def replay_data(segment, open_log):
  from cereal import log

  data = None
  for filename in ("rlog.bz2", "qlog.bz2"):
    try:
      with ExitStack() as stack:
        source = stack.enter_context(open_log(segment, filename))
        reader = stack.enter_context(bz2.BZ2File(source))
        raw = reader.read(MAX_LOG_BYTES + 1)
        if len(raw) <= MAX_LOG_BYTES and raw:
          data = raw
          del raw
          break
        del raw
    except (OSError, EOFError):
      continue
  if data is None:
    raise RuntimeError("읽을 수 있는 로그가 없거나 크기 제한을 초과했습니다.")

  start = None
  video_start = None
  points = {}
  for event in log.Event.read_multiple_bytes(data):
    stamp = event.logMonoTime
    if start is None:
      start = stamp
    kind = event.which()
    if kind == "roadEncodeIdx" and video_start is None and event.roadEncodeIdx.timestampEof:
      video_start = event.roadEncodeIdx.timestampEof
    if not event.valid or kind not in ("carState", "carControl", "radarState"):
      continue
    tick = (stamp - start) // 100_000_000
    if not 0 <= tick < MAX_REPLAY_SECONDS * 10:
      continue
    point = points.setdefault(tick, {})
    if kind == "carState":
      point.update(speed=finite(event.carState.vEgo, 3.6), accel=finite(event.carState.aEgo))
    elif kind == "carControl":
      point["target"] = finite(event.carControl.actuators.accel)
    else:
      lead = event.radarState.leadOne
      point.update(leadSpeed=finite(lead.vLead, 3.6) if lead.status else None,
                   distance=finite(lead.dRel) if lead.status else None)
  if not points:
    raise RuntimeError("이 구간에 재생할 주행 데이터가 없습니다.")
  return {"points": [[tick / 10, points[tick]] for tick in sorted(points)],
          "videoOffset": (video_start - start) / 1e9 if video_start is not None else None}
