"""On-demand road camera and bounded EON route replay."""

import bz2
import io
import math
import re
import time
from contextlib import ExitStack

VIDEO_FILE = "qcamera.mp4"
# 10 fps like the EON road view (NvgWindow frame_divider=2). The S9 draws the
# lines itself, so a frame costs only a crop and a JPEG here.
# 4 fps: at 10 fps the S20's Wi-Fi traffic broke up its Bluetooth audio (navi voice,
# music; 2026-11-14) and the EON encoded a JPEG every 100 ms.
FRAME_INTERVAL = 1 / 4
MAX_LOG_BYTES = 32 * 1024 * 1024
MAX_REPLAY_SECONDS = 120

# EON onroad view (selfdrive/ui): a 1920x1080 panel minus the 20 px border,
# with the road camera magnified by ZOOM / focal around the frame centre.
EON_SCREEN = (1880.0, 1040.0)
EON_ZOOM = 2138.5
CLIP_MARGIN = 1000.0
MIN_DRAW_DISTANCE = 10.0
MAX_DRAW_DISTANCE = 100.0
HUD_VIEW_DEFAULT = (384, 535)
HUD_VIEW_MAX = 640
SUPERSAMPLE = 2
LEAD_BOX_MIN_W = 180.0
LEAD_BOX_MAX_W = 320.0


def road_path_color(status_color, engaged, lead_detected, accel):
  # NvgWindow::drawLaneLines
  if not status_color:
    return (26, 190, 255, 178)  # HSL(197°, 100%, 55%), alpha 70%
  if not engaged:
    return (0, 0, 0, 120)
  if not lead_detected:
    return (0, 153, 0, 120)
  if not math.isfinite(accel) or abs(accel) < 0.5:
    return (255, 255, 0, 120)
  if accel >= 0.5:
    return (255, 153, 0, 120)
  return (255, 0, 0, 120)


def eon_view(camera_size, view_size, focal):
  """Camera crop of the EON screen region a HUD pane of view_size shows.

  The pane is filled like the EON fills its screen: the full EON height when
  the pane is narrower than the EON, the full EON width otherwise. Returns the
  integer camera crop, the output size and the EON-screen -> output affine.
  """
  zoom = EON_ZOOM / focal
  screen_w, screen_h = EON_SCREEN
  aspect = view_size[0] / view_size[1]
  if aspect <= screen_w / screen_h:
    crop_w, crop_h = screen_h * aspect, screen_h
  else:
    crop_w, crop_h = screen_w, screen_w / aspect
  cam_w, cam_h = camera_size
  u0 = int(round(cam_w / 2 + (-crop_w / 2) / zoom))
  v0 = int(round(cam_h / 2 + (-crop_h / 2) / zoom))
  u1 = int(round(cam_w / 2 + (crop_w / 2) / zoom))
  v1 = int(round(cam_h / 2 + (crop_h / 2) / zoom))
  u0, v0, u1, v1 = max(0, u0), max(0, v0), min(cam_w, u1), min(cam_h, v1)
  native = (u1 - u0, v1 - v0)
  fit = min(1.0, view_size[0] / native[0], view_size[1] / native[1])
  output = (max(1, int(round(native[0] * fit))), max(1, int(round(native[1] * fit))))
  # EON screen x of camera column u0, and output pixels per EON pixel.
  origin = (screen_w / 2 + (u0 - cam_w / 2) * zoom, screen_h / 2 + (v0 - cam_h / 2) * zoom)
  scale = (output[0] / (native[0] * zoom), output[1] / (native[1] * zoom))
  return (u0, v0, u1, v1), output, origin, scale


def camera_view(camera_size, view_size):
  """Full-height camera crop in the HUD pane's shape (the framing the HUD showed before
  2026-11-12). The EON-screen zoom (eon_view) made the HUD's narrow pane show only the
  middle of the EON view, about twice as large. Returns the crop and the output size."""
  cam_w, cam_h = camera_size
  aspect = view_size[0] / view_size[1]
  crop_w, crop_h = min(cam_w, cam_h * aspect), min(cam_h, cam_w / aspect)
  u0 = int(round((cam_w - crop_w) / 2))
  v0 = int(round((cam_h - crop_h) / 2))
  u1, v1 = int(round(u0 + crop_w)), int(round(v0 + crop_h))
  native = (u1 - u0, v1 - v0)
  fit = min(1.0, view_size[0] / native[0], view_size[1] / native[1])
  output = (max(1, int(round(native[0] * fit))), max(1, int(round(native[1] * fit))))
  return (u0, v0, u1, v1), output


class EonProjector:
  """calib_frame_to_full_frame from selfdrive/ui/ui.cc, in EON screen pixels."""

  def __init__(self, rpy, camera_size, focal):
    import numpy as np
    from common.transformations.camera import fcam_intrinsics, get_view_frame_from_calib_frame
    self.np = np
    self.matrix = fcam_intrinsics @ get_view_frame_from_calib_frame(*rpy, 0.0)[:, :3]
    self.zoom = EON_ZOOM / focal
    self.cx = camera_size[0] / 2
    self.cy = camera_size[1] / 2

  def points(self, xyz):
    np = self.np
    xyz = np.asarray(xyz, dtype=float).reshape(-1, 3)
    projected = xyz @ self.matrix.T
    depth = projected[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
      u = projected[:, 0] / depth
      v = projected[:, 1] / depth
    x = EON_SCREEN[0] / 2 + (u - self.cx) * self.zoom
    y = EON_SCREEN[1] / 2 + (v - self.cy) * self.zoom
    valid = (np.isfinite(xyz).all(axis=1) & (depth > 1e-6) & np.isfinite(x) & np.isfinite(y)
             & (x >= -CLIP_MARGIN) & (x <= EON_SCREEN[0] + CLIP_MARGIN)
             & (y >= -CLIP_MARGIN) & (y <= EON_SCREEN[1] + CLIP_MARGIN))
    return [(float(px), float(py)) if ok else None for px, py, ok in zip(x, y, valid)]


def path_length_idx(xs, distance):
  max_idx = 0
  for i in range(1, len(xs)):
    if xs[i] > distance:
      break
    max_idx = i
  return max_idx


def line_polygon(projector, xs, ys, zs, y_off, z_off_left, z_off_right, last, allow_invert=True, max_x=None):
  # update_line_data / update_mpc_path_data: left edge, then right edge reversed.
  count = min(len(xs), len(ys), len(zs), last + 1)
  if count <= 0:
    return []
  left = projector.points([(xs[i], ys[i] - y_off, zs[i] + z_off_left) for i in range(count)])
  right = projector.points([(xs[i], ys[i] + y_off, zs[i] + z_off_right) for i in range(count)])
  lefts, rights = [], []
  for i in range(count):
    x = xs[i]
    if not math.isfinite(x) or x < 0 or (max_x is not None and x > max_x):
      continue
    if left[i] is None or right[i] is None:
      continue
    if not allow_invert and lefts and left[i][1] > lefts[-1][1]:
      continue
    lefts.append(left[i])
    rights.append(right[i])
  return lefts + rights[::-1]


def split_path(polygon):
  # NvgWindow::drawLaneLines: C3 mode 14, 45 % / 10 % gap / 45 %.
  count = len(polygon)
  half = count // 2
  if half < 2 or count % 2:
    return [polygon] if count >= 3 else []
  left_band, right_band = [None] * count, [None] * count
  for i in range(half):
    lx, ly = polygon[i]
    rx, ry = polygon[count - 1 - i]
    left_band[i] = (lx, ly)
    right_band[i] = (lx + (rx - lx) * 0.55, ly + (ry - ly) * 0.55)
    left_band[count - 1 - i] = (lx + (rx - lx) * 0.45, ly + (ry - ly) * 0.45)
    right_band[count - 1 - i] = (rx, ry)
  return [left_band, right_band]


def _clip_path_bottom(polygon, bottom):
  clipped = []
  previous = polygon[-1]
  for point in polygon:
    if (point[1] <= bottom) != (previous[1] <= bottom):
      fraction = (bottom - previous[1]) / (point[1] - previous[1])
      clipped.append((previous[0] + fraction * (point[0] - previous[0]), bottom))
    if point[1] <= bottom:
      clipped.append(point)
    previous = point
  return clipped


def _rounded_rect(draw, box, radius, fill=None, outline=None, width=1):
  try:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
  except (AttributeError, TypeError, ValueError):
    draw.rectangle(box, fill=fill, outline=outline)


class RoadPreview:
  def __init__(self):
    self.client = None
    self.frame_array = None
    self.camera_size = None
    self.captured_ms = 0
    self.images = {}
    self.subscriber = None
    self.updated = 0
    self.requested = 0
    self.hud_requested = 0
    self.params = None
    self.next_param_read = 0.0
    self.path_status_color = True
    self.path_width = 1.0
    self.lead_box = None
    self.lead_box_time = 0.0
    self.fonts = {}

  def expire(self):
    now = time.monotonic()
    if now - self.requested > 3:
      self.client = None
      self.frame_array = None
      self.images.clear()
    if now - self.hud_requested > 3:
      self.subscriber = None
      self.lead_box = None

  def _capture(self):
    import numpy as np
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
    raw = np.frombuffer(data.tobytes(), dtype=np.uint8)
    if raw.size < stride * height:
      return
    self.camera_size = (width, height)
    self.captured_ms = int(time.time() * 1000)
    self.frame_array = raw[:stride * height].reshape(height, stride)

  def _image(self, box=None):
    import numpy as np
    from PIL import Image
    width, height = self.camera_size
    u0, v0, u1, v1 = box or (0, 0, width, height)
    bgr = self.frame_array[v0:v1, u0 * 3:u1 * 3].reshape(v1 - v0, u1 - u0, 3)
    return Image.fromarray(np.ascontiguousarray(bgr[:, :, ::-1]))

  def _read_params(self, now):
    if now < self.next_param_read:
      return
    self.next_param_read = now + 1.0
    if self.params is None:
      from common.params import Params
      self.params = Params()
    value = self.params.get("ShowPathStatusColor")
    try:
      self.path_status_color = bool(int(value)) if value else True
    except ValueError:
      self.path_status_color = False
    value = self.params.get("ShowPathWidth")
    try:
      width = int(value) if value else 100
    except ValueError:
      width = 100
    self.path_width = min(1.5, max(0.3, width / 100.0 if width > 0 else 1.0))

  def _font(self, size):
    if size not in self.fonts:
      from PIL import ImageFont
      try:
        import os
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts", "opensans_regular.ttf")
        self.fonts[size] = ImageFont.truetype(path, size)
      except (OSError, ImportError):
        self.fonts[size] = ImageFont.load_default()
    return self.fonts[size]

  def _hud_frame(self, view_size, path_end):
    from PIL import Image, ImageDraw
    from cereal import messaging
    from common.transformations.camera import FOCAL

    box, output, origin, scale = eon_view(self.camera_size, view_size, FOCAL)
    image = self._image(box)
    if image.size != output:
      image = image.resize(output, Image.BILINEAR)

    if self.subscriber is None:
      self.subscriber = messaging.SubMaster(["controlsState", "modelV2", "lateralPlan", "liveCalibration",
                                             "radarState", "longitudinalPlan"])
    sm = self.subscriber
    sm.update(0)
    now = messaging.sec_since_boot()

    def fresh(name, age):
      return sm.valid[name] and sm.logMonoTime[name] and 0 <= now - sm.logMonoTime[name] / 1e9 < age

    # UIState::worldObjectsVisible: the EON draws once calibration arrives.
    if not fresh("modelV2", 0.25) or not fresh("liveCalibration", 3.0):
      self.lead_box = None
      return image
    rpy = list(sm["liveCalibration"].rpyCalib)
    if len(rpy) != 3 or not all(map(math.isfinite, rpy)):
      return image
    self._read_params(now)

    projector = EonProjector(rpy, self.camera_size, FOCAL)
    model = sm["modelV2"]
    radar = sm["radarState"] if fresh("radarState", 0.5) else None
    engaged = fresh("controlsState", 0.5) and sm["controlsState"].enabled

    ss = SUPERSAMPLE
    big = image.resize((output[0] * ss, output[1] * ss), Image.NEAREST)
    draw = ImageDraw.Draw(big, "RGBA")
    ox, oy = origin
    kx, ky = scale[0] * ss, scale[1] * ss

    def to_out(points):
      return [((x - ox) * kx, (y - oy) * ky) for x, y in points]

    def polygon(points, fill):
      if len(points) >= 3:
        draw.polygon(to_out(points), fill=fill)

    position = model.position
    pos_x, pos_y, pos_z = list(position.x), list(position.y), list(position.z)
    if len(pos_x) < 2:
      return image
    max_distance = min(MAX_DRAW_DISTANCE, max(MIN_DRAW_DISTANCE, pos_x[-1]))

    lane_lines = list(model.laneLines)
    lane_probs = list(model.laneLineProbs)
    max_idx = path_length_idx(list(lane_lines[0].x), max_distance) if lane_lines else 0
    for line, prob in zip(lane_lines, lane_probs):
      if not math.isfinite(prob):
        continue
      alpha = int(round(255 * min(0.7, max(0.0, prob))))
      polygon(line_polygon(projector, list(line.x), list(line.y), list(line.z), 0.025 * prob, 0, 0, max_idx),
              (255, 255, 255, alpha))
    for line, std in zip(model.roadEdges, model.roadEdgeStds):
      if not math.isfinite(std):
        continue
      alpha = int(round(255 * min(1.0, max(0.0, 1.0 - std))))
      polygon(line_polygon(projector, list(line.x), list(line.y), list(line.z), 0.025, 0, 0, max_idx),
              (255, 0, 0, alpha))

    lead_one = radar.leadOne if radar is not None else None
    lead_detected = lead_one is not None and lead_one.status
    if lead_detected:
      max_distance = min(max(lead_one.dRel, 0.0), max_distance)
    track = []
    if fresh("lateralPlan", 0.5) and sm["lateralPlan"].mpcSolutionValid:
      plan = sm["lateralPlan"]
      mpc_x, mpc_y = list(plan.mpcPathX), list(plan.mpcPathY)
      count = min(len(mpc_x), len(mpc_y), len(pos_x))
      if count >= 2:
        track = line_polygon(projector, mpc_x[:count], mpc_y[:count], pos_z[:count], self.path_width, 1.22, 1.22,
                             count - 1, allow_invert=False, max_x=max_distance)
        if len(track) < 4:
          track = []
    if not track:
      track = line_polygon(projector, pos_x, pos_y, pos_z, self.path_width, 1.22, 1.22,
                           path_length_idx(pos_x, max_distance), allow_invert=False)

    accel = 0.0
    if lead_detected and fresh("longitudinalPlan", 0.5) and len(sm["longitudinalPlan"].accels):
      accel = sm["longitudinalPlan"].accels[0]
    color = road_path_color(self.path_status_color, engaged, lead_detected, accel)
    bottom = (output[1] * ss - 1) * path_end / 1000
    for band in split_path(track):
      band = _clip_path_bottom(to_out(band), bottom)
      if len(band) >= 3:
        draw.polygon(band, fill=color)

    self._draw_leads(draw, projector, radar, model, pos_x, pos_z, to_out, (kx + ky) / 2, now)
    return big.reduce(ss) if hasattr(big, "reduce") else big.resize(output, Image.BOX)

  def _draw_leads(self, draw, projector, radar, model, pos_x, pos_z, to_out, k, now):
    # NvgWindow::drawCarrotLead, in EON screen pixels.
    if radar is None:
      self.lead_box = None
      return

    def lead_edges(lead):
      z = pos_z[path_length_idx(pos_x, lead.dRel)] + 1.22
      left, right = projector.points([(lead.dRel, -lead.yRel - 1.2, z), (lead.dRel, -lead.yRel + 1.2, z)])
      return (left, right) if left is not None and right is not None else None

    if radar.leadTwo.status:
      edges = lead_edges(radar.leadTwo)
      if edges is not None:
        (xl, ly), (xr, _) = edges
        w2 = min(LEAD_BOX_MAX_W * 0.8, max(80.0, xr - xl))
        (x0, y0), (x1, y1) = to_out([(xl - 10, ly - w2 * 0.8), (xl + w2 + 10, ly)])
        _rounded_rect(draw, (x0, y0, x1, y1), 15 * k, fill=(0, 0, 0, 10), outline=(218, 111, 37, 255),
                      width=max(1, int(round(2 * k))))

    lead = radar.leadOne
    edges = lead_edges(lead) if lead.status else None
    if edges is None:
      self.lead_box = None
      return
    (xl, ly), (xr, _) = edges
    raw = (min(LEAD_BOX_MAX_W, max(LEAD_BOX_MIN_W, xr - xl)),
           min(EON_SCREEN[0] - 200.0, max(200.0, (xl + xr) / 2)),
           min(EON_SCREEN[1] - 120.0, max(150.0, ly)))
    if self.lead_box is None:
      self.lead_box = raw
    else:
      # The EON filters at 10 Hz with 0.85; keep the same time constant here.
      a = 0.85 ** min(10.0, max(0.0, (now - self.lead_box_time) * 10.0))
      self.lead_box = tuple(old * a + new * (1.0 - a) for old, new in zip(self.lead_box, raw))
    self.lead_box_time = now
    w, cx, ly = self.lead_box
    stroke = (255, 175, 3, 255) if lead.radar else (0, 0, 255, 255)
    (x0, y0), (x1, y1) = to_out([(cx - w / 2 - 10, ly - w * 0.8), (cx + w / 2 + 10, ly)])
    _rounded_rect(draw, (x0, y0, x1, y1), 15 * k, fill=(0, 0, 0, 10), outline=stroke,
                  width=max(1, int(round(3 * k))))

    dy = ly + 60
    leads_v3 = list(model.leadsV3)
    vision = leads_v3[0].x[0] if leads_v3 and leads_v3[0].prob > 0.5 and len(leads_v3[0].x) else 0.0
    font = self._font(max(8, int(round(40 * k))))
    for distance, x, fill in ((lead.dRel if lead.radar else 0.0, cx - 125, (255, 175, 3, 255)),
                              (vision, cx + 35, (0, 0, 255, 255))):
      if not distance > 0:
        continue
      (x0, y0), (x1, y1) = to_out([(x, dy - 35), (x + 90, dy + 7)])
      _rounded_rect(draw, (x0, y0, x1, y1), 15 * k, fill=fill)
      text = "%.1f" % distance
      try:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
      except AttributeError:
        right, bottom = draw.textsize(text, font=font)
        left = top = 0
      draw.text(((x0 + x1 - (right - left)) / 2 - left, (y0 + y1 - (bottom - top)) / 2 - top), text,
                font=font, fill=(255, 255, 255, 255))

  def hud_camera(self, view_size=None):
    """EON-view camera crop without lines, for HUDs that draw the lines themselves.

    Returns (jpeg, camera crop box, capture time in epoch ms) or None. The S9
    projects the telemetry captured closest to that time onto this crop.
    """
    from common.transformations.camera import FOCAL

    now = time.monotonic()
    self.requested = now
    if now - self.updated >= FRAME_INTERVAL:
      self.updated = now
      self.frame_array = None
      self.images.clear()
      self._capture()
    if self.frame_array is None:
      return None
    view_size = view_size or HUD_VIEW_DEFAULT
    variant = ("camera", view_size)
    if variant not in self.images:
      from PIL import Image
      box, output = camera_view(self.camera_size, view_size)
      image = self._image(box)
      if image.size != output:
        image = image.resize(output, Image.BILINEAR)
      data = io.BytesIO()
      image.save(data, "JPEG", quality=60)
      self.images[variant] = (data.getvalue(), box, self.captured_ms)
    return self.images[variant]

  def frame(self, hud=False, path_end=1000, view_size=None):
    now = time.monotonic()
    self.requested = now
    if hud:
      self.hud_requested = now
    if now - self.updated >= FRAME_INTERVAL:
      self.updated = now
      self.frame_array = None
      self.images.clear()
      self._capture()
    if self.frame_array is None:
      return None
    view_size = view_size or HUD_VIEW_DEFAULT
    variant = (path_end, view_size) if hud else None
    if variant not in self.images:
      if hud:
        image = self._hud_frame(view_size, path_end)
      else:
        image = self._image()
        image.thumbnail((480, 360))
      output = io.BytesIO()
      image.save(output, "JPEG", quality=70 if hud else 55)
      self.images[variant] = output.getvalue()
    return self.images[variant]


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
