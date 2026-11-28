#!/usr/bin/env python3
"""
camerad for the Galaxy S20 FE: frames from the Android camera bridge (s20cam, ring files in /s20share) go out as
VisionIPC road frames and narrowRoadCameraState / wideRoadCameraState messages, like the comma 3 camerad.

The bridge makes the frames look like comma 3 frames (1928x1208, 2648 px road focal length), so with
deviceType "pc" and sensor "unknown" openpilot uses its comma 3 camera config. The S20 FE camera runs at a fixed
30 fps (the HAL ignores a 50 ms frame duration), so frames are picked on a 50 ms grid: 2 of every 3, 20 Hz on average.
"""
import os, sys, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ring import Ring, boottime_ns  # noqa: E402

from msgq.visionipc import VisionIpcServer  # noqa: E402
from openpilot.cereal import messaging  # noqa: E402
from openpilot.cereal.visionipc import VisionStreamType  # noqa: E402
from openpilot.system.camerad.cameras.nv12_info import get_nv12_info  # noqa: E402

SHARE = os.getenv("S20_SHARE", "/s20share")
PERIOD_NS = 50_000_000
VIPC_BUFFERS = 18
CAMERAS = [  # (ring file, message, stream)
  ("road.ring", "narrowRoadCameraState", VisionStreamType.VISION_STREAM_NARROW_ROAD),
  ("wide.ring", "wideRoadCameraState", VisionStreamType.VISION_STREAM_WIDE_ROAD),
]


class Picker:
  """Choose frames on a 50 ms grid from a faster camera: a frame is sent once it is within half a frame of the next slot."""
  def __init__(self):
    self.due = None

  def take(self, ts: int, frame_ns: int) -> bool:
    if self.due is None or ts - self.due > 2 * PERIOD_NS:  # first frame, or we fell behind: restart the grid
      self.due = ts
    if ts >= self.due - frame_ns // 2:
      self.due += PERIOD_NS
      return True
    return False


class Cam:
  def __init__(self, path: str, msg: str, stream):
    self.ring, self.msg, self.stream = Ring(path), msg, stream
    w, h = self.ring.width, self.ring.height
    self.stride, self.y_height, self.uv_height, self.size = get_nv12_info(w, h)
    self.uv_offset = self.stride * self.y_height
    self.buf = np.zeros(self.size, np.uint8)
    self.y = self.buf[:self.stride * h].reshape(h, self.stride)
    self.uv = self.buf[self.uv_offset:self.uv_offset + self.stride * (h // 2)].reshape(h // 2, self.stride)
    self.last_id, self.frame_id, self.picker, self.frame_ns = self.ring.latest_id(), 0, Picker(), 33_333_333
    self.prev_ts = None

  def to_vipc(self, nv12: bytes):
    w, h = self.ring.width, self.ring.height
    src = np.frombuffer(nv12, np.uint8)
    self.y[:, :w] = src[:w * h].reshape(h, w)
    self.uv[:, :w] = src[w * h:w * h * 3 // 2].reshape(h // 2, w)
    return self.buf


def main():
  cams = []
  for name, msg, stream in CAMERAS:
    path = os.path.join(SHARE, name)
    if not os.path.exists(path):
      print(f"s20camerad: no {path}, {msg} not published")
      continue
    c = Cam(path, msg, stream)
    # a ring left over from an earlier run (e.g. the wide camera) would make modeld wait for frames that never come
    fresh = c.ring.wait_next(c.ring.latest_id() - 1, timeout=3.0)
    if fresh is None or boottime_ns() - fresh[2] > 2_000_000_000:
      print(f"s20camerad: {path} is not being written (stale or camera off), {msg} not published")
      continue
    cams.append(c)
  if not cams:
    raise SystemExit(f"s20camerad: no camera rings in {SHARE}; start s20cam on the Android side first")

  pm = messaging.PubMaster([c.msg for c in cams])
  vipc = VisionIpcServer("camerad")
  for c in cams:
    vipc.create_buffers_with_sizes(c.stream, VIPC_BUFFERS, c.ring.width, c.ring.height, c.size, c.stride, c.uv_offset)
    print(f"s20camerad: {c.msg} {c.ring.width}x{c.ring.height} focal {c.ring.focal:.1f} px, stride {c.stride}")
  vipc.start_listener()

  sent, t_log = 0, time.monotonic()
  while True:
    idle = True
    for c in cams:
      fid = c.ring.latest_id()
      if fid <= c.last_id:
        continue
      idle = False
      r = c.ring.read(fid)
      c.last_id = fid
      if r is None:
        continue
      _, sensor_ts, written_ns, nv12 = r
      if c.prev_ts is not None and 0 < sensor_ts - c.prev_ts < 200_000_000:
        c.frame_ns = (c.frame_ns * 7 + (sensor_ts - c.prev_ts)) // 8
      c.prev_ts = sensor_ts
      if not c.picker.take(sensor_ts, c.frame_ns):
        continue
      vipc.send(c.stream, c.to_vipc(nv12), c.frame_id, sensor_ts, sensor_ts + 1_000_000)
      dat = messaging.new_message(c.msg, valid=True)
      fd = getattr(dat, c.msg)
      fd.frameId = c.frame_id
      fd.frameIdSensor = fid
      fd.timestampSof = sensor_ts
      fd.timestampEof = sensor_ts + 1_000_000
      fd.processingTime = (boottime_ns() - written_ns) / 1e9
      fd.transform = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
      pm.send(c.msg, dat)
      c.frame_id += 1
      sent += 1
    if time.monotonic() - t_log > 10:
      print(f"s20camerad: {sent / (time.monotonic() - t_log):.1f} frames/s sent")
      sent, t_log = 0, time.monotonic()
    if idle:
      time.sleep(0.002)


if __name__ == "__main__":
  main()
