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


def draw_road_overlay(image, model, lateral_plan, rpy, camera_size):
  import numpy as np
  from PIL import ImageDraw
  from common.transformations.camera import FULL_FRAME_SIZE, fcam_intrinsics, get_view_frame_from_calib_frame
  from selfdrive.eon_cluster.scene import final_lateral_path

  if camera_size != FULL_FRAME_SIZE:
    return
  scale = np.diag([image.width / camera_size[0], image.height / camera_size[1], 1.0])
  projection = scale @ fcam_intrinsics @ get_view_frame_from_calib_frame(*rpy, 0.0)[:, :3]

  def project(points, y_offset=0.0, z_offset=0.0):
    xyz = np.asarray(points, dtype=float).reshape(-1, 3)
    xyz = xyz[np.isfinite(xyz).all(axis=1) & (xyz[:, 0] > 1.0) & (xyz[:, 0] <= 100.0)]
    xyz += [0.0, y_offset, z_offset]
    pixels = xyz @ projection.T
    pixels = pixels[pixels[:, 2] > 0.5]
    return [tuple(point) for point in pixels[:, :2] / pixels[:, 2:3]]

  def xyz(line):
    return list(zip(line.x, line.y, line.z))

  draw = ImageDraw.Draw(image, "RGBA")
  path = final_lateral_path(lateral_plan, model, model.position.t) or xyz(model.position)
  left = project(path, -1.0, 1.22)
  right = project(path, 1.0, 1.22)
  if len(left) > 1 and len(right) > 1:
    draw.polygon(left + right[::-1], fill=(34, 212, 83, 125))
  for line, probability in zip(model.laneLines, model.laneLineProbs):
    if not math.isfinite(probability) or probability < 0.15:
      continue
    points = project(xyz(line))
    if len(points) > 1:
      draw.line(points, fill=(250, 250, 250, int(230 * min(1.0, probability))), width=2)


class RoadPreview:
  def __init__(self):
    self.client = None
    self.thumbnail = None
    self.camera_size = None
    self.images = {}
    self.subscriber = None
    self.updated = 0
    self.requested = 0
    self.hud_requested = 0

  def expire(self):
    now = time.monotonic()
    if now - self.requested > 3:
      self.client = None
      self.thumbnail = None
      self.images.clear()
    if now - self.hud_requested > 3:
      self.subscriber = None

  def _capture(self):
    from PIL import Image
    from cereal.visionipc.visionipc_pyx import VisionIpcClient, VisionStreamType

    if self.client is None:
      client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_RGB_ROAD, True)
      if not client.connect(False):
        return
      self.client = client
    data = self.client.recv(100)
    if data is None:
      self.client = None
      return
    width, height, stride = self.client.width, self.client.height, self.client.stride
    self.camera_size = (width, height)
    self.thumbnail = Image.frombytes("RGB", self.camera_size, data.tobytes(), "raw", "BGR", stride)
    self.thumbnail.thumbnail((480, 360))

  def _overlay(self):
    from cereal import messaging

    if self.subscriber is None:
      self.subscriber = messaging.SubMaster(["controlsState", "modelV2", "lateralPlan", "liveCalibration"])
    sm = self.subscriber
    sm.update(0)
    now = messaging.sec_since_boot()
    if (not sm.valid["controlsState"]
        or not 0 <= now - sm.logMonoTime["controlsState"] / 1e9 < 0.25
        or not sm["controlsState"].enabled):
      return self.thumbnail
    if not sm.valid["modelV2"] or not 0 <= now - sm.logMonoTime["modelV2"] / 1e9 < 0.25:
      return self.thumbnail
    calibration = sm["liveCalibration"]
    rpy = list(calibration.rpyCalib)
    # Calibration.CALIBRATED is 1 in this branch.
    if (not sm.valid["liveCalibration"] or calibration.calStatus != 1
        or not 0 <= now - sm.logMonoTime["liveCalibration"] / 1e9 < 3.0):
      return self.thumbnail
    if len(rpy) != 3 or not all(map(math.isfinite, rpy)):
      return self.thumbnail
    plan_fresh = 0 <= now - sm.logMonoTime["lateralPlan"] / 1e9 < 0.25
    plan = sm["lateralPlan"] if plan_fresh and sm.valid["lateralPlan"] else None
    image = self.thumbnail.copy()
    draw_road_overlay(image, sm["modelV2"], plan, rpy, self.camera_size)
    return image

  def frame(self, hud=False):
    now = time.monotonic()
    self.requested = now
    if hud:
      self.hud_requested = now
    if now - self.updated >= FRAME_INTERVAL:
      self.updated = now
      self.thumbnail = None
      self.images.clear()
      self._capture()
    if self.thumbnail is None:
      return None
    if hud not in self.images:
      image = self._overlay() if hud else self.thumbnail
      output = io.BytesIO()
      image.save(output, "JPEG", quality=55)
      self.images[hud] = output.getvalue()
    return self.images[hud]


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
