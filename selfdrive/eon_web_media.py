"""On-demand road camera and bounded EON route replay."""

import bz2
import io
import math
import re
import time
from contextlib import ExitStack

VIDEO_FILE = "qcamera.mp4"
FRAME_INTERVAL = 1 / 3
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
