import io
import json
import math
import os
import time
from collections import OrderedDict

from PIL import Image, ImageDraw, ImageFont


NAVI_STATE = "/dev/shm/carrot_navi_route.json"
NAVI_MAP = "/dev/shm/carrot_navi_map.jpg"
NAVI_LANE = "/dev/shm/carrot_navi_lane_bottom.png"
NAVI_TBT_CURRENT = "/dev/shm/carrot_navi_tbt_current.png"
NAVI_TBT_NEXT = "/dev/shm/carrot_navi_tbt_next.png"
NAVI_MAX_AGE_MS = 35000
NAVI_ROUTE_MAX_AGE_MS = 3000
NAVI_ROUTE_GRACE_S = 2.0
BITMAP_FONT_DATA = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", "Pretendard-SemiBold.fnt")
BITMAP_FONT_IMAGE = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", "Pretendard-SemiBold.png")
HANGUL_BITMAP_FONT_DATA = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", "Pretendard-Hangul.fnt")
HANGUL_BITMAP_FONT_IMAGE = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", "Pretendard-Hangul.png")
STEERING_WHEEL_IMAGE = os.path.join(os.path.dirname(__file__), "..", "assets", "img_chffr_wheel.png")
_FONT_CACHE = {}
_BITMAP_FONT_KEYS = set()
_BITMAP_TEXT_CACHE = OrderedDict()
_BITMAP_TEXT_CACHE_LIMIT = 256
_BITMAP_ATLAS = None
_BITMAP_ATLAS_LOAD_ATTEMPTED = False
_HANGUL_BITMAP_ATLAS = None
_HANGUL_BITMAP_ATLAS_LOAD_ATTEMPTED = False
_IMAGE_CACHE = {}
_FIT_CACHE = {}


def _font(size, bold=False):
  key = (int(size), bool(bold))
  cached = _FONT_CACHE.get(key)
  if cached is not None:
    return cached
  candidates = [
    "/system/fonts/NotoSansCJK-Bold.ttc" if bold else "/system/fonts/NotoSansCJK-Regular.ttc",
    "/system/fonts/Roboto-Bold.ttf" if bold else "/system/fonts/Roboto-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
  ]
  for path in candidates:
    try:
      font = ImageFont.truetype(path, size)
      _FONT_CACHE[key] = font
      _BITMAP_FONT_KEYS.discard(key)
      return font
    except (IOError, OSError, ImportError):
      pass
  try:
    font = ImageFont.load_default()
  except ImportError:
    # Newer Pillow versions may route load_default() through FreeType too.
    # Keep a true bitmap fallback for minimal EON Pillow builds.
    bitmap_loader = getattr(ImageFont, "load_default_imagefont", None)
    if bitmap_loader is None:
      raise
    font = bitmap_loader()
  _FONT_CACHE[key] = font
  _BITMAP_FONT_KEYS.add(key)
  return font


def _font_attributes(line):
  attributes = {}
  for token in line.strip().split()[1:]:
    if "=" not in token:
      continue
    key, value = token.split("=", 1)
    attributes[key] = value.strip('"')
  return attributes


def _read_bitmap_atlas(data_path, image_path):
  try:
    glyphs = {}
    line_height = 0
    with open(data_path, "r", encoding="utf-8") as font_file:
      for line in font_file:
        if line.startswith("common "):
          line_height = int(_font_attributes(line)["lineHeight"])
        elif line.startswith("char "):
          values = _font_attributes(line)
          glyphs[int(values["id"])] = {
            key: int(values[key])
            for key in ("x", "y", "width", "height", "xoffset", "yoffset", "xadvance")
          }
    if line_height <= 0 or not glyphs:
      raise ValueError("invalid bitmap font metadata")
    with Image.open(image_path) as source:
      alpha = source.getchannel("A").copy() if "A" in source.getbands() else source.convert("L")
    return alpha, glyphs, line_height
  except (IOError, OSError, KeyError, TypeError, ValueError):
    return None


def _load_bitmap_atlas():
  """Load the carrot-wip Latin BMFont atlas without Pillow FreeType."""
  global _BITMAP_ATLAS, _BITMAP_ATLAS_LOAD_ATTEMPTED
  if not _BITMAP_ATLAS_LOAD_ATTEMPTED:
    _BITMAP_ATLAS_LOAD_ATTEMPTED = True
    _BITMAP_ATLAS = _read_bitmap_atlas(BITMAP_FONT_DATA, BITMAP_FONT_IMAGE)
  return _BITMAP_ATLAS


def _load_hangul_bitmap_atlas():
  """Lazily load complete Hangul glyph coverage only when Korean is drawn."""
  global _HANGUL_BITMAP_ATLAS, _HANGUL_BITMAP_ATLAS_LOAD_ATTEMPTED
  if not _HANGUL_BITMAP_ATLAS_LOAD_ATTEMPTED:
    _HANGUL_BITMAP_ATLAS_LOAD_ATTEMPTED = True
    _HANGUL_BITMAP_ATLAS = _read_bitmap_atlas(HANGUL_BITMAP_FONT_DATA, HANGUL_BITMAP_FONT_IMAGE)
  return _HANGUL_BITMAP_ATLAS


def _atlas_text_mask(text, size):
  atlas = _load_bitmap_atlas()
  if atlas is None:
    return None
  image, glyphs, line_height = atlas
  fallback = glyphs.get(ord("?"))
  if fallback is None:
    return None
  selected = []
  hangul_atlas = None
  for character in str(text):
    glyph = glyphs.get(ord(character))
    glyph_image = image
    glyph_line_height = line_height
    if glyph is None and ord(character) > 0x7F:
      if hangul_atlas is None:
        hangul_atlas = _load_hangul_bitmap_atlas()
      if hangul_atlas is not None:
        hangul_image, hangul_glyphs, hangul_line_height = hangul_atlas
        glyph = hangul_glyphs.get(ord(character))
        if glyph is not None:
          glyph_image = hangul_image
          glyph_line_height = hangul_line_height
    selected.append((glyph_image, glyph or fallback, glyph_line_height))
  render_height = float(max(1, int(size)))
  width = max(1, int(math.ceil(sum(
    glyph["xadvance"] * render_height / glyph_line_height
    for _, glyph, glyph_line_height in selected
  ) + render_height)))
  height = max(1, int(math.ceil(render_height)))
  mask = Image.new("L", (width, height), 0)
  cursor = 0.0
  resampling = getattr(Image, "Resampling", Image)
  for glyph_image, glyph, glyph_line_height in selected:
    scale = render_height / glyph_line_height
    glyph_width = glyph["width"]
    glyph_height = glyph["height"]
    if glyph_width > 0 and glyph_height > 0:
      crop = glyph_image.crop((glyph["x"], glyph["y"], glyph["x"] + glyph_width, glyph["y"] + glyph_height))
      target_width = max(1, int(round(glyph_width * scale)))
      glyph_target_height = max(1, int(round(glyph_height * scale)))
      crop = crop.resize((target_width, glyph_target_height), resampling.BILINEAR)
      paste_x = int(round(cursor + glyph["xoffset"] * scale))
      paste_y = int(round(glyph["yoffset"] * scale))
      mask.paste(crop, (paste_x, paste_y))
    cursor += glyph["xadvance"] * scale
  content_bbox = mask.getbbox()
  return mask.crop(content_bbox) if content_bbox is not None else mask


def _bitmap_text_mask(text, size, bold, font):
  """Render a scalable BMFont, with Pillow's fixed font as last resort."""
  text = str(text)
  cache_key = (text, int(size), bool(bold))
  cached = _BITMAP_TEXT_CACHE.get(cache_key)
  if cached is not None:
    _BITMAP_TEXT_CACHE.move_to_end(cache_key)
    return cached

  atlas_mask = _atlas_text_mask(text, size)
  if atlas_mask is not None:
    _BITMAP_TEXT_CACHE[cache_key] = atlas_mask
    _BITMAP_TEXT_CACHE.move_to_end(cache_key)
    while len(_BITMAP_TEXT_CACHE) > _BITMAP_TEXT_CACHE_LIMIT:
      _BITMAP_TEXT_CACHE.popitem(last=False)
    return atlas_mask

  # Pillow's built-in bitmap font is normally ASCII-only. Preserve readable
  # HUD numbers and labels when a navigation string contains unsupported
  # glyphs instead of failing the entire frame.
  try:
    if hasattr(font, "getbbox"):
      font.getbbox(text)
    elif hasattr(font, "getsize"):
      font.getsize(text)
  except (UnicodeEncodeError, ValueError):
    text = text.encode("ascii", "replace").decode("ascii")

  probe = Image.new("L", (1, 1), 0)
  probe_draw = ImageDraw.Draw(probe)
  try:
    bbox = probe_draw.textbbox((0, 0), text, font=font)
  except AttributeError:
    width, height = probe_draw.textsize(text, font=font)
    bbox = (0, 0, width, height)
  except (UnicodeEncodeError, ValueError):
    text = text.encode("ascii", "replace").decode("ascii")
    try:
      bbox = probe_draw.textbbox((0, 0), text, font=font)
    except AttributeError:
      width, height = probe_draw.textsize(text, font=font)
      bbox = (0, 0, width, height)

  pad = 2 if bold else 1
  width = max(1, int(bbox[2] - bbox[0]) + pad * 2 + (1 if bold else 0))
  height = max(1, int(bbox[3] - bbox[1]) + pad * 2)
  mask = Image.new("L", (width, height), 0)
  mask_draw = ImageDraw.Draw(mask)
  origin = (pad - int(bbox[0]), pad - int(bbox[1]))
  mask_draw.text(origin, text, font=font, fill=255)
  if bold:
    mask_draw.text((origin[0] + 1, origin[1]), text, font=font, fill=255)
  content_bbox = mask.getbbox()
  if content_bbox is not None:
    mask = mask.crop(content_bbox)

  target_height = max(1, int(size))
  target_width = max(1, int(round(mask.width * target_height / float(max(1, mask.height)))))
  resampling = getattr(Image, "Resampling", Image)
  mask = mask.resize((target_width, target_height), resampling.NEAREST)
  _BITMAP_TEXT_CACHE[cache_key] = mask
  _BITMAP_TEXT_CACHE.move_to_end(cache_key)
  while len(_BITMAP_TEXT_CACHE) > _BITMAP_TEXT_CACHE_LIMIT:
    _BITMAP_TEXT_CACHE.popitem(last=False)
  return mask


def _draw_text(draw, xy, text, size, bold=False, fill=(255, 255, 255), anchor="la"):
  """Draw scalable text, including on EON Pillow builds without _imagingft."""
  key = (int(size), bool(bold))
  font = _font(size, bold)
  if key not in _BITMAP_FONT_KEYS:
    draw.text(xy, str(text), font=font, fill=fill, anchor=anchor)
    return

  mask = _bitmap_text_mask(text, size, bold, font)
  x, y = int(round(xy[0])), int(round(xy[1]))
  horizontal = anchor[0] if anchor else "l"
  vertical = anchor[1] if anchor and len(anchor) > 1 else "a"
  if horizontal == "m":
    x -= mask.width // 2
  elif horizontal == "r":
    x -= mask.width
  if vertical == "m":
    y -= mask.height // 2
  elif vertical in ("s", "b", "d"):
    y -= mask.height
  draw.bitmap((x, y), mask, fill=fill)


def _text_width(text, size, bold=False):
  key = (int(size), bool(bold))
  font = _font(size, bold)
  if key in _BITMAP_FONT_KEYS:
    return _bitmap_text_mask(text, size, bold, font).width
  try:
    return int(math.ceil(font.getlength(str(text))))
  except (AttributeError, TypeError, ValueError):
    bbox = font.getbbox(str(text))
    return max(0, int(bbox[2] - bbox[0]))


def _draw_stroked_text(draw, xy, text, size, bold=False, fill=(255, 255, 255),
                       stroke_fill=(0, 0, 0), stroke_width=3, anchor="la"):
  stroke_width = max(0, int(stroke_width))
  if stroke_width:
    x, y = xy
    offsets = ((-stroke_width, 0), (stroke_width, 0), (0, -stroke_width), (0, stroke_width),
               (-stroke_width, -stroke_width), (-stroke_width, stroke_width),
               (stroke_width, -stroke_width), (stroke_width, stroke_width))
    for dx, dy in offsets:
      _draw_text(draw, (x + dx, y + dy), text, size, bold, stroke_fill, anchor)
  _draw_text(draw, xy, text, size, bold, fill, anchor)


def read_navi_state(path=NAVI_STATE):
  try:
    with open(path, "r") as f:
      state = json.load(f)
  except (IOError, ValueError):
    return {}
  updated_at = int(state.get("updated_at_ms", 0) or 0)
  if updated_at <= 0 or abs(int(time.time() * 1000) - updated_at) > NAVI_MAX_AGE_MS:
    return {}
  return state


def _navi_route_active(navi, now_ms=None):
  route = navi.get("route") or {}
  if not isinstance(route, dict):
    return False

  status = navi.get("navigation_status")
  if isinstance(status, dict):
    for key in ("active", "is_active", "isActive", "navigating", "is_navigating", "isNavigating",
                "route_active", "routeActive"):
      if key in status and not bool(status.get(key)):
        return False
    state = str(status.get("state", status.get("status", "")) or "").strip().lower()
    if state in ("idle", "inactive", "off", "stopped", "ended", "none"):
      return False

  stream_times = navi.get("stream_updated_at_ms") or {}
  route_updated_at = int(stream_times.get("route", 0) or 0) if isinstance(stream_times, dict) else 0
  if route_updated_at > 0:
    wall_now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    route_age = wall_now - route_updated_at
    if route_age < -5000 or route_age > NAVI_ROUTE_MAX_AGE_MS:
      return False

  try:
    remain_distance = float(route.get("remain_distance_m", 0.0) or 0.0)
  except (TypeError, ValueError):
    remain_distance = 0.0
  return remain_distance > 0.0


def _safe_image(path):
  try:
    stat = os.stat(path)
    signature = (stat.st_mtime, stat.st_size)
    cached = _IMAGE_CACHE.get(path)
    if cached is not None and cached[0] == signature:
      return cached[1]
    with Image.open(path) as source:
      # Keep TMap overlay transparency. Converting lane PNGs to RGB turned
      # their transparent canvas into a large black strip over the map.
      image = source.convert("RGBA" if "A" in source.getbands() else "RGB")
    _IMAGE_CACHE[path] = (signature, image)
    return image
  except (IOError, OSError, ValueError):
    _IMAGE_CACHE.pop(path, None)
    return None


def _fit_cover(image, size):
  target_w, target_h = size
  scale = max(float(target_w) / image.width, float(target_h) / image.height)
  resized = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.BILINEAR)
  left = max(0, (resized.width - target_w) // 2)
  top = max(0, (resized.height - target_h) // 2)
  return resized.crop((left, top, left + target_w, top + target_h))


def _fit_cached(path, size, mode, build):
  """Cache one scaled result per (path, size, mode).

  The file signature is stored as the cache *value*, never as part of the key.
  Keying on the signature made every new TMap frame allocate a fresh entry that
  was never released, which grew by roughly a megabyte per received frame.
  """
  key = (path, int(size[0]), int(size[1]), mode)
  image = _safe_image(path)
  if image is None:
    _FIT_CACHE.pop(key, None)
    return None
  signature = _IMAGE_CACHE[path][0]
  cached = _FIT_CACHE.get(key)
  if cached is not None and cached[0] == signature:
    return cached[1]
  fitted = build(image)
  _FIT_CACHE[key] = (signature, fitted)
  return fitted


def _safe_fitted_image(path, size):
  return _fit_cached(path, size, "cover", lambda image: _fit_cover(image, size))


def _safe_contained_image(path, size):
  """Scale an overlay inside the target box without cropping or distortion."""
  def build(image):
    scale = min(float(size[0]) / image.width, float(size[1]) / image.height)
    resampling = getattr(Image, "Resampling", Image)
    return image.resize((max(1, int(round(image.width * scale))),
                         max(1, int(round(image.height * scale)))), resampling.BILINEAR)
  return _fit_cached(path, size, "contain", build)


def _safe_full_image(path, size):
  """Resize the complete source frame without cropping any map edge."""
  def build(image):
    resampling = getattr(Image, "Resampling", Image)
    return image.resize((max(1, int(size[0])), max(1, int(size[1]))), resampling.BILINEAR)
  return _fit_cached(path, size, "full", build)


def _clamp(value, low, high):
  return max(low, min(high, value))


def _speed_value(speed_kph, is_metric):
  return float(speed_kph) if is_metric else float(speed_kph) * 0.621371


def _distance_text(distance_m, is_metric, language="ko", prefix=False):
  distance_m = max(0.0, float(distance_m))
  if is_metric:
    value = ("%.1f km" % (distance_m / 1000.0)) if distance_m >= 1000.0 else ("%d m" % int(round(distance_m)))
  else:
    distance_miles = distance_m / 1609.344
    value = ("%.1f mi" % distance_miles) if distance_miles >= 0.1 else ("%d ft" % int(round(distance_m * 3.28084)))
  if not prefix:
    return value
  return (("남은 거리 " if language == "ko" else "Remaining ") + value)


class HudRenderer(object):
  DRIVE_RATIO = 0.40
  SYSTEM_RATIO = 0.20
  MAX_DISTANCE_M = 120.0

  def __init__(self, width, height, jpeg_quality=58):
    self.width = int(width)
    self.height = int(height)
    self.jpeg_quality = 58
    self.set_jpeg_quality(jpeg_quality)
    self.mirror = False
    self._route_visible_until = 0.0
    # Last complete guidance strings, reused while the JSON file is replaced.
    self._navi_text_cache = {}
    # Cache the static carrot-style road surface per panel size.
    self._road_backgrounds = {}
    # Display-only temporal history. Model coordinates remain untouched; only
    # the external HUD receives this low-pass filter.
    self._geometry_history = {"lanes": {}, "edges": {}, "path": {}}
    self._lane_hold_frames = {}
    # Antialiased vehicle images are built once, then reused in 4 px size
    # buckets so liveTracks do not redraw complex car geometry every frame.
    self._vehicle_base_sprites = {}
    self._vehicle_sprite_cache = OrderedDict()
    # The real Genesis wheel is resized once and rotation is bucketed so a
    # moving steering angle does not add a full PNG transform every frame.
    self._steering_wheel_base = None
    self._steering_wheel_scaled = {}
    self._steering_wheel_cache = OrderedDict()

  def set_jpeg_quality(self, jpeg_quality):
    jpeg_quality = max(1, min(95, int(jpeg_quality)))
    changed = jpeg_quality != self.jpeg_quality
    self.jpeg_quality = jpeg_quality
    return changed

  def set_mirror(self, mirror):
    mirror = bool(mirror)
    changed = mirror != self.mirror
    self.mirror = mirror
    return changed

  @staticmethod
  def _interpolate_lateral(points, longitudinal):
    if not points:
      return None
    if longitudinal <= points[0][0]:
      return float(points[0][1])
    for index in range(1, len(points)):
      x0, y0 = points[index - 1]
      x1, y1 = points[index]
      if longitudinal <= x1:
        span = max(1e-3, float(x1) - float(x0))
        mix = _clamp((float(longitudinal) - float(x0)) / span, 0.0, 1.0)
        return float(y0) + (float(y1) - float(y0)) * mix
    return float(points[-1][1])

  def _stabilize_polylines(self, items, channel):
    history = self._geometry_history[channel]
    stabilized, next_history = [], {}
    seen_keys = set()
    for position, item in enumerate(items or []):
      points = [(float(x), float(y)) for x, y in item.get("points", [])
                if math.isfinite(float(x)) and math.isfinite(float(y))]
      if not points:
        continue
      key = item.get("index", position)
      seen_keys.add(key)
      previous = history.get(key)
      filtered = []
      if previous:
        near_now = self._interpolate_lateral(points, min(8.0, points[-1][0]))
        near_previous = self._interpolate_lateral(previous, min(8.0, points[-1][0]))
        # A line association change should snap once, not drag a wrong line
        # across the panel for several frames.
        if near_now is not None and near_previous is not None and abs(near_now - near_previous) > 2.4:
          previous = None
      for x, y in points:
        old_y = self._interpolate_lateral(previous, x) if previous else None
        if old_y is None:
          filtered_y = y
        else:
          # Far model points create the biggest screen sweep in a bend, so
          # limit their per-frame travel more strongly while keeping the near
          # lane responsive to the car's actual turn.
          max_step = 0.22 + 0.010 * min(self.MAX_DISTANCE_M, max(0.0, x))
          limited_y = old_y + _clamp(y - old_y, -max_step, max_step)
          current_weight = 0.42 - 0.16 * min(1.0, max(0.0, x) / self.MAX_DISTANCE_M)
          filtered_y = old_y + (limited_y - old_y) * current_weight
        filtered.append((x, filtered_y))
      copied = dict(item)
      copied["points"] = filtered
      stabilized.append(copied)
      next_history[key] = filtered
      if channel == "lanes":
        self._lane_hold_frames[key] = 0

    if channel == "lanes":
      next_hold_frames = {}
      for key in seen_keys:
        next_hold_frames[key] = 0
      # Hold a missing model line for at most three display frames. This
      # prevents a one-frame probability drop from blinking, without inventing
      # the permanent two-line fallback used by the old cluster.
      for key, previous in history.items():
        if key in next_history:
          continue
        missed = self._lane_hold_frames.get(key, 0) + 1
        if missed <= 3:
          stabilized.append({"index": key, "points": previous,
                             "probability": max(0.45, 0.62 - missed * 0.05)})
          next_history[key] = previous
          next_hold_frames[key] = missed
      self._lane_hold_frames = next_hold_frames

    self._geometry_history[channel] = next_history
    return stabilized

  def _stabilize_scene_geometry(self, scene):
    """Stabilize detected lanes and path; road edges are intentionally hidden."""
    stabilized = dict(scene)
    stabilized["lanes"] = self._stabilize_polylines(scene.get("lanes", []), "lanes")
    stabilized["edges"] = []
    self._geometry_history["edges"] = {}
    path = [{"index": 0, "points": scene.get("path", [])}]
    filtered_path = self._stabilize_polylines(path, "path")
    stabilized["path"] = filtered_path[0]["points"] if filtered_path else []
    return stabilized

  def _project(self, panel, longitudinal, lateral):
    left, top, right, bottom = panel
    distance = _clamp(float(longitudinal), 0.0, self.MAX_DISTANCE_M)
    depth = distance / self.MAX_DISTANCE_M
    perspective = math.pow(max(0.0, 1.0 - depth), 1.35)
    horizon = top + int((bottom - top) * 0.10)
    screen_y = horizon + (bottom - horizon) * perspective
    pixels_per_meter = 4.0 + 66.0 * perspective
    center_x = (left + right) * 0.5
    screen_x = center_x - float(lateral) * pixels_per_meter
    return int(round(screen_x)), int(round(screen_y))

  def _project_line(self, panel, points):
    projected = []
    for longitudinal, lateral in points:
      if 0.0 <= longitudinal <= self.MAX_DISTANCE_M:
        point = self._project(panel, longitudinal, lateral)
        if not projected or point != projected[-1]:
          projected.append(point)
    return projected

  @staticmethod
  def _ego_lane_indices(lanes):
    """Positions in `lanes` of the two markings that bound the driving lane."""
    if not lanes:
      return set()
    indexed = [position for position, lane in enumerate(lanes)
               if lane.get("index") in (1, 2)]
    if indexed:
      return set(indexed)
    # No model index available (fallback lanes, replays): fall back to the two
    # markings closest to the car laterally at the near end of the polyline.
    offsets = []
    for position, lane in enumerate(lanes):
      points = lane.get("points", [])
      if not points:
        continue
      nearest = min(points, key=lambda point: point[0])
      offsets.append((abs(float(nearest[1])), position))
    offsets.sort()
    return {position for _, position in offsets[:2]}

  def _fallback_lanes(self):
    distances = (0.0, 8.0, 18.0, 32.0, 52.0, 78.0, 110.0)
    return [
      {"points": [(distance, -1.8) for distance in distances], "probability": 0.65},
      {"points": [(distance, 1.8) for distance in distances], "probability": 0.65},
    ]

  def _draw_polyline(self, draw, panel, points, fill, width):
    projected = self._project_line(panel, points)
    if len(projected) >= 2:
      draw.line(projected, fill=fill, width=max(1, int(width)), joint="curve")

  def _draw_road_surface(self, image, panel):
    """Cached light-gray production-cluster road with no artificial edges."""
    left, top, right, bottom = panel
    size = (max(1, right - left), max(1, bottom - top))
    background = self._road_backgrounds.get(size)
    if background is None:
      width, height = size
      background = Image.new("RGB", size, (233, 236, 238))
      road = ImageDraw.Draw(background)
      horizon = int(height * 0.105)
      for band in range(12):
        y0 = int(horizon * band / 12.0)
        y1 = int(horizon * (band + 1) / 12.0) + 1
        mix = band / 11.0
        shade = int(244 - 10 * mix)
        road.rectangle((0, y0, width, y1), fill=(shade, shade + 1, shade + 2))

      center = width * 0.5
      far_half = max(9, width * 0.052)
      near_half = width * 0.48
      road.polygon(((center - far_half, horizon), (center + far_half, horizon),
                    (center + near_half, height), (center - near_half, height)),
                   fill=(220, 224, 226))
      for band in range(14):
        near_t = math.pow(band / 14.0, 1.22)
        far_t = math.pow((band + 1) / 14.0, 1.22)
        y0 = int(horizon + (height - horizon) * near_t)
        y1 = int(horizon + (height - horizon) * far_t) + 1
        half0 = far_half + (near_half - far_half) * near_t
        half1 = far_half + (near_half - far_half) * far_t
        shade = 222 - int(band * 0.55) - (band % 2)
        road.polygon(((center - half0, y0), (center + half0, y0),
                      (center + half1, y1), (center - half1, y1)),
                     fill=(shade, shade + 2, shade + 3))
      for step in (0.20, 0.36, 0.53, 0.70, 0.86):
        y = int(horizon + (height - horizon) * step)
        half = far_half + (near_half - far_half) * step
        road.line((int(center - half), y, int(center + half), y),
                  fill=(198, 203, 206), width=1)
      self._road_backgrounds[size] = background
    image.paste(background, (left, top))

  @staticmethod
  def _segment_quad(screen_a, screen_b, width_a, width_b):
    """Return a perspective-width quad around one projected world segment."""
    dx = float(screen_b[0] - screen_a[0])
    dy = float(screen_b[1] - screen_a[1])
    length = math.hypot(dx, dy)
    if length < 0.5:
      return None
    nx, ny = -dy / length, dx / length
    half_a, half_b = width_a * 0.5, width_b * 0.5
    return (
      (screen_a[0] + nx * half_a, screen_a[1] + ny * half_a),
      (screen_b[0] + nx * half_b, screen_b[1] + ny * half_b),
      (screen_b[0] - nx * half_b, screen_b[1] - ny * half_b),
      (screen_a[0] - nx * half_a, screen_a[1] - ny * half_a),
    )

  @staticmethod
  def _lane_dash_fragments(points, dash_m=4.0, gap_m=5.0):
    """Split a model curve into fixed world-distance dash fragments."""
    period = max(0.2, float(dash_m) + float(gap_m))
    ordered = sorted(((float(x), float(y)) for x, y in points), key=lambda point: point[0])
    fragments = []
    for index in range(len(ordered) - 1):
      x0, y0 = ordered[index]
      x1, y1 = ordered[index + 1]
      if x1 <= x0:
        continue
      cursor = math.floor(x0 / period) * period
      while cursor < x1:
        start_x = max(x0, cursor)
        end_x = min(x1, cursor + dash_m)
        if end_x > start_x + 1e-3:
          span = x1 - x0
          start_mix = (start_x - x0) / span
          end_mix = (end_x - x0) / span
          start_y = y0 + (y1 - y0) * start_mix
          end_y = y0 + (y1 - y0) * end_mix
          fragments.append(((start_x, start_y), (end_x, end_y)))
        cursor += period
    return fragments

  def _draw_lane_marking(self, draw, panel, points, color, probability):
    """Draw model-detected lanes as pale perspective dash segments."""
    probability = _clamp(float(probability), 0.0, 1.0)
    visible = [(x, y) for x, y in points if 0.0 <= x <= self.MAX_DISTANCE_M]
    for world_a, world_b in self._lane_dash_fragments(visible):
      screen_a = self._project(panel, world_a[0], world_a[1])
      screen_b = self._project(panel, world_b[0], world_b[1])
      depth_a = math.pow(max(0.0, 1.0 - world_a[0] / self.MAX_DISTANCE_M), 1.18)
      depth_b = math.pow(max(0.0, 1.0 - world_b[0] / self.MAX_DISTANCE_M), 1.18)
      width_a = max(1.0, 1.0 + (3.0 + 2.0 * probability) * depth_a)
      width_b = max(1.0, 1.0 + (3.0 + 2.0 * probability) * depth_b)
      quad = self._segment_quad(screen_a, screen_b, width_a, width_b)
      if quad:
        draw.polygon(quad, fill=color)

  def _draw_path(self, image, draw, panel, points, enabled=False, scene=None):
    """Draw two blue lane-width boundaries with no center trajectory fill."""
    if len(points) < 2:
      points = [(0.0, 0.0), (12.0, 0.0), (30.0, 0.0), (60.0, 0.0), (100.0, 0.0)]
    ordered = sorted((float(longitudinal), float(lateral)) for longitudinal, lateral in points)
    near_start = 2.2
    samples = []
    if ordered and ordered[-1][0] >= near_start:
      samples.append((near_start, self._interpolate_lateral(ordered, near_start)))
    samples.extend((longitudinal, lateral) for longitudinal, lateral in ordered if longitudinal > near_start)
    left_edge, right_edge = [], []
    for longitudinal, lateral in samples:
      if 0.0 <= longitudinal <= self.MAX_DISTANCE_M:
        # Keep the model path as the centerline, but place the visible lines at
        # the approximate lane boundaries requested by the cluster sketch.
        half_width = 1.75
        left_edge.append(self._project(panel, longitudinal, lateral + half_width))
        right_edge.append(self._project(panel, longitudinal, lateral - half_width))
    width = max(3, self.height // 110)
    if len(left_edge) >= 2:
      draw.line(left_edge, fill=(24, 126, 224), width=width, joint="curve")
    if len(right_edge) >= 2:
      draw.line(right_edge, fill=(24, 126, 224), width=width, joint="curve")

  def _build_vehicle_base_sprite(self, style, braking=False, marker=False):
    """Build a wide rear-perspective sedan matching the reference cluster."""
    alpha = 220 if marker else 255
    palettes = {
      "ego": ((86, 91, 95), (39, 43, 47), (111, 116, 120)),
      "lead": ((178, 182, 185), (96, 100, 103), (204, 207, 209)),
      "traffic": ((124, 130, 134), (65, 70, 74), (153, 157, 160)),
      "blindspot": ((235, 238, 240), (132, 140, 146), (255, 255, 255)),
    }
    body, dark, raised = palettes.get(style, palettes["traffic"])
    rgba = lambda color, opacity=alpha: (color[0], color[1], color[2], opacity)
    sprite = Image.new("RGBA", (240, 190), (0, 0, 0, 0))
    car = ImageDraw.Draw(sprite)

    wheel = rgba((7, 9, 11))
    for wheel_box in ((22, 58, 42, 116), (198, 58, 218, 116),
                      (18, 126, 42, 174), (198, 126, 222, 174)):
      car.rounded_rectangle(wheel_box, radius=6, fill=wheel)

    # The near rear bumper is wider than the roof, producing the short, broad
    # sedan shape seen in the reference instead of a long top-down vehicle.
    body_shape = ((76, 15), (164, 15), (188, 31), (207, 62),
                  (219, 116), (211, 162), (193, 181), (47, 181),
                  (29, 162), (21, 116), (33, 62), (52, 31))
    car.polygon(body_shape, fill=rgba(body))
    car.line(body_shape + (body_shape[0],), fill=rgba(dark), width=5, joint="curve")

    if style == "ego":
      # Side mirrors are only worthwhile on the large ego sprite.
      car.polygon(((34, 75), (8, 70), (2, 83), (29, 94)), fill=rgba(dark))
      car.polygon(((206, 75), (232, 70), (238, 83), (211, 94)), fill=rgba(dark))

    # Flat side faces retain depth but add no highlight or road shadow.
    car.polygon(((74, 19), (48, 37), (30, 73), (25, 119), (34, 158),
                 (53, 176), (64, 148), (57, 93), (74, 45)), fill=rgba(dark))
    side_right = (max(0, dark[0] + 9), max(0, dark[1] + 9), max(0, dark[2] + 9))
    car.polygon(((166, 19), (192, 37), (210, 73), (215, 119), (206, 158),
                 (187, 176), (176, 148), (183, 93), (166, 45)), fill=rgba(side_right))

    glass = rgba((21, 34, 43))
    glass_side = rgba((29, 43, 52))
    car.polygon(((72, 39), (168, 39), (185, 91), (55, 91)), fill=glass)
    car.polygon(((57, 96), (78, 91), (76, 132), (55, 143)), fill=glass_side)
    car.polygon(((183, 96), (162, 91), (164, 132), (185, 143)), fill=glass_side)
    car.polygon(((78, 88), (162, 88), (166, 132), (74, 132)), fill=rgba(raised))
    car.polygon(((62, 135), (178, 135), (166, 158), (74, 158)), fill=glass)
    car.polygon(((49, 158), (191, 158), (184, 179), (56, 179)), fill=rgba(raised))

    # The reference uses an unlit neutral vehicle model. Keep only the dark
    # rear bumper; no tail-lamp or braking-light primitives are drawn.
    car.line((66, 179, 174, 179), fill=rgba((13, 17, 20)), width=5)
    return sprite

  def _vehicle_sprite(self, style, car_w, car_h, braking=False, marker=False):
    bucket_w = max(16, int(round(float(car_w) / 4.0)) * 4)
    bucket_h = max(18, int(round(float(car_h) / 4.0)) * 4)
    base_key = (str(style), bool(braking), bool(marker))
    base = self._vehicle_base_sprites.get(base_key)
    if base is None:
      base = self._build_vehicle_base_sprite(*base_key)
      self._vehicle_base_sprites[base_key] = base

    key = base_key + (bucket_w, bucket_h)
    sprite = self._vehicle_sprite_cache.get(key)
    if sprite is None:
      resampling = getattr(Image, "Resampling", Image)
      sprite = base.resize((bucket_w, bucket_h), resampling.LANCZOS)
      self._vehicle_sprite_cache[key] = sprite
      while len(self._vehicle_sprite_cache) > 48:
        self._vehicle_sprite_cache.popitem(last=False)
    else:
      self._vehicle_sprite_cache.move_to_end(key)
    return sprite

  def _draw_vehicle_shape(self, image, cx, cy, car_w, car_h,
                          style="traffic", braking=False, marker=False):
    """Paste a cached realistic car with no shadow or gloss."""
    sprite = self._vehicle_sprite(style, car_w, car_h, braking, marker)
    x = int(round(cx - sprite.width * 0.5))
    y = int(round(cy - sprite.height))
    image.paste(sprite, (x, y), sprite)

  def _draw_world_block(self, draw, cx, cy, width, height, color):
    """Low 3D cuboid used for stationary liveTracks, as in carrot-wip."""
    width, height = max(8, int(width)), max(6, int(height))
    lift = max(3, height // 3)
    skew = max(2, width // 7)
    left, right = cx - width // 2, cx + width // 2
    top, bottom = cy - height, cy
    front = ((left, top + lift), (right, top + lift), (right, bottom), (left, bottom))
    side = ((right, top + lift), (right + skew, top), (right + skew, bottom - lift), (right, bottom))
    cap = ((left, top + lift), (left + skew, top), (right + skew, top), (right, top + lift))
    draw.polygon(front, fill=tuple(max(8, int(channel * 0.56)) for channel in color))
    draw.polygon(side, fill=tuple(max(6, int(channel * 0.38)) for channel in color))
    draw.polygon(cap, fill=color)

  def _draw_lead(self, image, draw, panel, lead, primary, radar_info=2, is_metric=True):
    distance = float(lead.get("distance", 0.0) or 0.0)
    lateral = float(lead.get("lateral", 0.0) or 0.0)
    if distance <= 0.0 or distance > self.MAX_DISTANCE_M:
      return
    cx, cy = self._project(panel, distance, lateral)
    ego_w, ego_h = self._ego_vehicle_size(panel)
    # The primary lead is intentionally half the ego size. Secondary tracks
    # remain slightly smaller so the visual hierarchy stays unambiguous.
    ratio = 0.50 if primary else 0.42
    car_w = max(24, int(round(ego_w * ratio)))
    car_h = max(24, int(round(ego_h * ratio)))
    self._draw_vehicle_shape(image, cx, cy, car_w, car_h,
                             "lead" if primary else "traffic",
                             float(lead.get("relative_speed", 0.0) or 0.0) < -0.5)

  def _draw_radar_point(self, image, draw, panel, point, radar_info=2, is_metric=True):
    distance = float(point.get("distance", 0.0) or 0.0)
    if distance <= 0.0 or distance > self.MAX_DISTANCE_M:
      return
    cx, cy = self._project(panel, distance, float(point.get("lateral", 0.0) or 0.0))
    stationary = bool(point.get("stationary", False))
    color = (54, 207, 121) if stationary else (75, 177, 244)
    scale = math.pow(max(0.08, 1.0 - distance / self.MAX_DISTANCE_M), 1.08)
    radius = max(4, int(4 + 14 * scale))
    if stationary:
      # carrot-wip renders raw radar returns as low cubes on the road plane.
      self._draw_world_block(draw, cx, cy, radius * 2.2, radius * 1.8, color)
    else:
      self._draw_vehicle_shape(image, cx, cy, radius * 3.4, radius * 2.8,
                               "traffic",
                               float(point.get("relative_speed", 0.0) or 0.0) < -0.5,
                               marker=True)
    if radar_info <= 0 or (radar_info in (1, 2) and stationary):
      return
    relative_speed = _speed_value(float(point.get("relative_speed", 0.0) or 0.0) * 3.6, is_metric)
    if radar_info in (2, 4):
      label = "%s %+.0f" % (_distance_text(distance, is_metric), relative_speed)
    else:
      label = "%+.0f" % relative_speed
    _draw_text(draw, (cx, cy - radius * 2 - 5), label, max(11, self.height // 34), True,
               fill=color, anchor="ms")

  def _draw_turn_signals(self, draw, panel, blinkers):
    """Two flashing arrows beside the ego car. Polygons only, no text cost."""
    if not blinkers:
      return
    left, top, right, bottom = panel
    if not (blinkers.get("left") or blinkers.get("right")):
      return
    if int(time.time() * 2) % 2:
      return
    cx = (left + right) // 2
    cy = bottom - int((bottom - top) * 0.20)
    size = max(14, (right - left) // 46)
    gap = max(60, (right - left) // 7)
    color = (72, 226, 118)
    if blinkers.get("left"):
      tip = cx - gap - size
      draw.polygon(((tip, cy), (tip + size, cy - size), (tip + size, cy + size)), fill=color)
    if blinkers.get("right"):
      tip = cx + gap + size
      draw.polygon(((tip, cy), (tip - size, cy - size), (tip - size, cy + size)), fill=color)

  @staticmethod
  def _ego_vehicle_size(panel):
    panel_w = panel[2] - panel[0]
    panel_h = panel[3] - panel[1]
    # About 30 percent smaller than the previous 112x104 minimum.
    return max(78, int(panel_w * 0.080)), max(73, int(panel_h * 0.30))

  def _draw_ego_vehicle(self, image, panel, enabled):
    cx, cy = self._project(panel, 2.4, 0.0)
    car_w, car_h = self._ego_vehicle_size(panel)
    self._draw_vehicle_shape(image, cx, cy - 9, car_w, car_h, "ego")

  def _draw_blindspot_indicator(self, draw, panel, side):
    """Draw the requested dark-gray BSD bracket instead of another car."""
    left, top, right, bottom = panel
    panel_w = right - left
    panel_h = bottom - top
    cx = (left + right) // 2
    x = cx - int(panel_w * 0.155) if side == "left" else cx + int(panel_w * 0.155)
    y = bottom - max(52, int(panel_h * 0.22))
    direction = 1 if side == "left" else -1
    color = (67, 73, 78)
    height = max(44, int(panel_h * 0.20))
    width = max(16, int(panel_w * 0.025))
    for offset in (0, direction * 10):
      points = ((x + offset + direction * width, y - height // 2),
                (x + offset + direction * 5, y - height // 4),
                (x + offset, y),
                (x + offset + direction * 5, y + height // 4),
                (x + offset + direction * width, y + height // 2))
      draw.line(points, fill=color, width=max(3, self.height // 150), joint="curve")

  def _draw_bipolar_gauge(self, draw, center_x, top, bottom, value, color, label, value_text):
    value = _clamp(float(value), -1.0, 1.0)
    width = max(43, self.height // 9)
    left = int(center_x - width * 0.5)
    right = int(center_x + width * 0.5)
    draw.rounded_rectangle((left, top, right, bottom), radius=max(8, width // 4),
                           fill=(5, 10, 16), outline=(138, 161, 179), width=2)
    center_y = (top + bottom) // 2
    draw.line((left + 3, center_y, right - 3, center_y), fill=(86, 102, 115), width=2)
    available = max(1, (bottom - top) // 2 - 7)
    fill_h = max(2, int(abs(value) * available)) if abs(value) > 0.01 else 0
    if fill_h:
      y0, y1 = ((center_y - fill_h, center_y) if value > 0 else (center_y, center_y + fill_h))
      draw.rounded_rectangle((left + 7, y0, right - 7, y1), radius=max(3, width // 8), fill=color)
    _draw_text(draw, (center_x, top - 8), value_text, max(12, self.height // 32), True,
               fill=color, anchor="ms")
    _draw_text(draw, (center_x, bottom + 7), label, max(12, self.height // 31), True,
               fill=(58, 66, 72), anchor="ma")

  def _draw_control_gauges(self, draw, box, scene):
    left, top, right, _ = box
    panel_w = right - left
    gauge_top = top + max(48, self.height // 9)
    gauge_bottom = gauge_top + max(86, int(self.height * 0.22))
    spacing = max(58, int(panel_w * 0.058))
    steer_x = right - max(39, int(panel_w * 0.038))
    accel_x = steer_x - spacing
    accel = float(scene.get("accel", 0.0) or 0.0)
    steer = float(scene.get("steer", 0.0) or 0.0)
    accel_normalized = _clamp(accel / 3.0, -1.0, 1.0)
    accel_color = (37, 211, 255) if accel >= 0.0 else (255, 78, 75)
    steer_color = (37, 211, 255) if steer >= 0.0 else (255, 177, 45)
    self._draw_bipolar_gauge(draw, accel_x, gauge_top, gauge_bottom, accel_normalized,
                             accel_color, "accel", "%+.2f" % accel)
    self._draw_bipolar_gauge(draw, steer_x, gauge_top, gauge_bottom, steer,
                             steer_color, "steer", "%+.0f%%" % (steer * 100.0))

  def _draw_clock(self, draw, box):
    left, top, _, _ = box
    x = left + 28
    y = top + 25
    draw.ellipse((x, y - 6, x + 12, y + 6), fill=(39, 219, 139))
    draw.arc((x + 16, y - 11, x + 39, y + 12), 205, 335, fill=(83, 194, 255), width=3)
    draw.arc((x + 21, y - 6, x + 34, y + 7), 205, 335, fill=(83, 194, 255), width=3)
    _draw_text(draw, (x + 46, y), time.strftime("%H:%M:%S"), max(18, self.height // 20), True,
               fill=(42, 49, 55), anchor="lm")

  def _draw_speed_limit(self, draw, x, y, limit):
    if limit <= 0:
      return
    radius = max(28, self.height // 15)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(250, 250, 250),
                 outline=(220, 45, 45), width=max(6, radius // 6))
    _draw_text(draw, (x, y), str(limit), max(24, radius), True, fill=(20, 20, 20), anchor="mm")

  def _steering_wheel_sprite(self, diameter, angle_deg):
    diameter = max(20, int(diameter))
    bucket = int(round(float(angle_deg) / 5.0) * 5)
    key = (diameter, bucket)
    sprite = self._steering_wheel_cache.get(key)
    if sprite is not None:
      self._steering_wheel_cache.move_to_end(key)
      return sprite
    if self._steering_wheel_base is None:
      source = _safe_image(STEERING_WHEEL_IMAGE)
      if source is None:
        return None
      self._steering_wheel_base = source.convert("RGBA")
    resampling = getattr(Image, "Resampling", Image)
    scaled = self._steering_wheel_scaled.get(diameter)
    if scaled is None:
      scaled = self._steering_wheel_base.resize((diameter, diameter), resampling.LANCZOS)
      self._steering_wheel_scaled[diameter] = scaled
    sprite = scaled.rotate(-bucket, resample=resampling.BICUBIC, expand=False)
    self._steering_wheel_cache[key] = sprite
    # 201 five-degree buckets cover the full -500..500 degree steering range.
    while len(self._steering_wheel_cache) > 208:
      self._steering_wheel_cache.popitem(last=False)
    return sprite

  def _draw_steering_wheel(self, image, draw, x, y, angle_deg, enabled):
    radius = max(25, self.height // 17)
    color = (18, 95, 225) if enabled else (118, 126, 132)
    width = max(4, radius // 6)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius),
                 fill=(238, 241, 243), outline=color, width=width)
    sprite = self._steering_wheel_sprite(radius * 2 - width * 2 - 5, angle_deg)
    if sprite is not None:
      image.paste(sprite, (int(x - sprite.width / 2), int(y - sprite.height / 2)), sprite)

  def _draw_gap_bars(self, draw, right_x, center_y, gap):
    """Draw the physical SCC GAP button state as one to four stacked bars."""
    gap = max(0, min(4, int(gap or 0)))
    bar_w = max(42, int(self.width * 0.024))
    bar_h = max(6, self.height // 60)
    spacing = max(3, bar_h // 2)
    _draw_text(draw, (right_x, center_y - 30), "GAP", max(10, self.height // 39), True,
               fill=(85, 94, 100), anchor="ms")
    for index in range(4):
      y1 = center_y + 26 - index * (bar_h + spacing)
      y0 = y1 - bar_h
      active = index < gap
      fill = (31, 168, 101) if active else (204, 209, 212)
      outline = (76, 126, 102) if active else (167, 174, 178)
      draw.rounded_rectangle((right_x - bar_w, y0, right_x, y1),
                             radius=max(2, bar_h // 2), fill=fill, outline=outline, width=1)

  def _draw_road_limit_badge(self, draw, center_x, center_y, limit):
    """Compact road-limit box used on the upper information row."""
    badge_w = max(58, int(self.width * 0.034))
    badge_h = max(32, int(self.height * 0.072))
    _draw_text(draw, (center_x, center_y - badge_h // 2 - 3), "LIMIT",
               max(10, self.height // 39), True, fill=(85, 94, 100), anchor="ms")
    draw.rounded_rectangle((center_x - badge_w // 2, center_y - badge_h // 2,
                            center_x + badge_w // 2, center_y + badge_h // 2),
                           radius=max(7, badge_h // 4), fill=(246, 247, 247),
                           outline=(114, 122, 128), width=2)
    text = str(int(round(limit))) if limit > 0 else "--"
    _draw_text(draw, (center_x, center_y), text, max(17, self.height // 22), True,
               fill=(54, 61, 66), anchor="mm")

  def _draw_requested_status_header(self, image, draw, box, speed_kph, cruise_kph, enabled, scene):
    left, top, right, _ = box
    panel_w = right - left
    center_x = (left + right) // 2
    is_metric = bool(scene.get("is_metric", True))
    language = "en" if str(scene.get("language", "ko")).lower() == "en" else "ko"
    display_speed = _speed_value(speed_kph, is_metric)
    display_cruise = _speed_value(cruise_kph, is_metric)

    speed_y = top + max(39, int(self.height * 0.095))
    _draw_text(draw, (center_x, speed_y), str(max(0, int(round(display_speed)))),
               max(64, int(self.height * 0.18)), False, fill=(28, 34, 39), anchor="mm")

    info_y = top + max(92, int(self.height * 0.215))
    gear = str(scene.get("gear", "--") or "--").upper()
    _draw_text(draw, (left + 28, info_y), gear, max(22, self.height // 19), True,
               fill=(55, 62, 67), anchor="lm")

    mode = int(scene.get("driving_mode", 0) or 0)
    mode_label, mode_color = {
      1: ("SAFE", (226, 144, 38)),
      2: ("ECO", (20, 160, 92)),
      3: ("NORM", (68, 76, 82)),
      4: ("FAST", (222, 67, 70)),
    }.get(mode, ("--", (104, 111, 116)))
    _draw_text(draw, (left + max(70, int(panel_w * 0.09)), info_y), mode_label,
               max(17, self.height // 23), True, fill=mode_color, anchor="lm")
    _draw_text(draw, (center_x, info_y), "KM" if is_metric else "MPH",
               max(13, self.height // 31), True, fill=(104, 111, 116), anchor="mm")

    road_limit = _speed_value(float(scene.get("road_limit_speed", 0) or 0), is_metric)
    self._draw_road_limit_badge(draw, right - max(132, int(panel_w * 0.17)), info_y, road_limit)
    self._draw_gap_bars(draw, right - 18, info_y, scene.get("cruise_gap", 0))

    separator_y = top + max(124, int(self.height * 0.28))
    draw.line((left + 18, separator_y, right - 18, separator_y),
              fill=(202, 207, 210), width=1)

    second_row_y = top + max(164, int(self.height * 0.37))
    left_x = left + max(92, int(panel_w * 0.14))
    right_x = right - max(92, int(panel_w * 0.14))
    self._draw_steering_wheel(image, draw, left_x, second_row_y,
                              float(scene.get("steering_angle_deg", 0.0) or 0.0), enabled)

    cruise_valid = enabled and 0.0 < cruise_kph < 255.0
    cruise_color = (18, 149, 224) if cruise_valid else (139, 147, 152)
    cruise_radius = max(28, self.height // 16)
    draw.ellipse((center_x - cruise_radius, second_row_y - cruise_radius,
                  center_x + cruise_radius, second_row_y + cruise_radius),
                 fill=(246, 247, 247), outline=cruise_color, width=max(4, cruise_radius // 7))
    cruise_text = str(int(round(display_cruise))) if cruise_valid else "--"
    _draw_text(draw, (center_x, second_row_y - 2), cruise_text, max(22, self.height // 17), True,
               fill=(47, 54, 59), anchor="mm")
    _draw_text(draw, (center_x, second_row_y + cruise_radius + 9), "SET",
               max(11, self.height // 35), True, fill=cruise_color, anchor="ma")
    camera_limit = _speed_value(float(scene.get("camera_limit_speed", 0) or 0), is_metric)
    self._draw_speed_limit(draw, right_x, second_row_y, int(round(camera_limit)))
    camera_distance = float(scene.get("camera_distance", 0) or 0)
    if camera_distance > 0:
      distance_text = _distance_text(camera_distance, is_metric, language)
      if bool(scene.get("camera_is_section", False)):
        distance_text = (("구간 " if language == "ko" else "SEC ") + distance_text)
      _draw_text(draw, (right_x, second_row_y + max(39, self.height // 11)), distance_text,
                 max(10, self.height // 38), True, fill=(104, 111, 116), anchor="ma")

  def _draw_driving_mode(self, draw, box, mode):
    modes = {
      1: ("SAFE", (255, 169, 45)),
      2: ("ECO", (40, 210, 125)),
      3: ("NORM", (235, 240, 245)),
      4: ("FAST", (235, 70, 70)),
    }
    if mode not in modes:
      return
    label, color = modes[mode]
    left, top, right, _ = box
    x = (left + right) // 2
    draw.rounded_rectangle((x - 58, top + 15, x + 58, top + 58), radius=12,
                           fill=(18, 25, 33), outline=color, width=2)
    _draw_text(draw, (x, top + 36), label, max(17, self.height // 23), True,
               fill=color, anchor="mm")

  def _draw_energy_mode(self, draw, box, energy_mode):
    energy_mode = str(energy_mode or "").upper()
    if energy_mode not in ("EV", "HEV", "PHEV"):
      return
    left, top, right, _ = box
    x = (left + right) // 2 + 92
    color = (75, 220, 145) if energy_mode == "EV" else (74, 183, 255)
    draw.rounded_rectangle((x - 42, top + 15, x + 42, top + 58), radius=12,
                           fill=(18, 25, 33), outline=color, width=2)
    _draw_text(draw, (x, top + 36), energy_mode, max(15, self.height // 27), True,
               fill=color, anchor="mm")

  @staticmethod
  def _bottom_card_box(box, side):
    left, top, right, bottom = box
    width = max(122, int((right - left) * 0.17))
    height = max(66, int((bottom - top) * 0.28))
    margin = 8
    x0 = left + margin if side == "left" else right - margin - width
    return x0, bottom - margin - height, x0 + width, bottom - margin

  @staticmethod
  def _valid_tpms(value):
    try:
      return value is not None and 5.0 <= float(value) <= 60.0
    except (TypeError, ValueError):
      return False

  def _draw_tpms(self, draw, box, tpms):
    card = self._bottom_card_box(box, "right")
    left, top, right, bottom = card
    draw.rounded_rectangle(card, radius=9, fill=(232, 235, 237),
                           outline=(158, 166, 171), width=2)
    _draw_text(draw, ((left + right) // 2, top + 5), "TPMS", max(9, self.height // 45), True,
               fill=(86, 94, 100), anchor="ma")
    values = [(tpms or {}).get(key) for key in ("fl", "fr", "rl", "rr")]
    center_x = (left + right) // 2
    center_y = top + int((bottom - top) * 0.60)
    car_w = max(15, int((right - left) * 0.17))
    car_h = max(27, int((bottom - top) * 0.45))
    draw.rounded_rectangle((center_x - car_w // 2, center_y - car_h // 2,
                            center_x + car_w // 2, center_y + car_h // 2),
                           radius=4, fill=(95, 102, 107), outline=(66, 73, 78), width=1)
    offsets = ((-34, -10), (34, -10), (-34, 13), (34, 13))
    for value, (dx, dy) in zip(values, offsets):
      valid = self._valid_tpms(value)
      text = str(int(round(float(value)))) if valid else "--"
      color = (211, 55, 61) if valid and float(value) < 31.0 else (59, 66, 71)
      _draw_text(draw, (center_x + dx, center_y + dy), text,
                 max(10, self.height // 38), True, fill=color, anchor="mm")

  def _draw_lead_info(self, draw, box, leads, is_metric, language):
    card = self._bottom_card_box(box, "left")
    left, top, right, bottom = card
    draw.rounded_rectangle(card, radius=9, fill=(232, 235, 237),
                           outline=(158, 166, 171), width=2)
    lead = leads[0] if leads else None
    distance = float(lead.get("distance", 0.0) or 0.0) if lead else 0.0
    relative_mps = float(lead.get("relative_speed", 0.0) or 0.0) if lead else 0.0
    if distance > 0.0:
      distance_value = distance if is_metric else distance / 0.3048
      distance_unit = "m" if is_metric else "ft"
      distance_text = "%d %s" % (int(round(distance_value)), distance_unit)
      relative_value = relative_mps * (3.6 if is_metric else 2.236936)
      relative_unit = "km/h" if is_metric else "mph"
      relative_text = "%+.0f %s" % (relative_value, relative_unit)
    else:
      distance_text = "--"
      relative_text = "--"
    label_color = (103, 111, 116)
    value_color = (54, 61, 66)
    row1_y = top + int((bottom - top) * 0.31)
    row2_y = top + int((bottom - top) * 0.72)
    lead_label = "앞차" if language == "ko" else "LEAD"
    relative_label = "상대" if language == "ko" else "REL"
    _draw_text(draw, (left + 8, row1_y), lead_label, max(9, self.height // 46), True,
               fill=label_color, anchor="lm")
    _draw_text(draw, (right - 8, row1_y), distance_text, max(11, self.height // 34), True,
               fill=value_color, anchor="rm")
    draw.line((left + 7, (top + bottom) // 2, right - 7, (top + bottom) // 2),
              fill=(195, 201, 204), width=1)
    _draw_text(draw, (left + 8, row2_y), relative_label, max(9, self.height // 46), True,
               fill=label_color, anchor="lm")
    _draw_text(draw, (right - 8, row2_y), relative_text, max(10, self.height // 39), True,
               fill=value_color, anchor="rm")

  def _draw_alert(self, draw, alert, box=None):
    if not alert or not (alert.get("text1") or alert.get("text2")):
      return
    left, top, right, bottom = box or (0, 0, self.width, self.height)
    text1 = " ".join(str(alert.get("text1", "") or "").split())
    text2 = " ".join(str(alert.get("text2", "") or "").split())
    if not text1:
      text1, text2 = text2, ""

    status = str(alert.get("status", "")).lower()
    critical = status in ("critical", "2") or "critical" in status
    prompt = status in ("userprompt", "warning", "1") or "prompt" in status or "warning" in status
    title_color = (255, 82, 96) if critical else (255, 174, 82) if prompt else (255, 255, 255)
    alert_size = str(alert.get("size", "")).lower()
    preferred_title_size = max(34, self.height // (8 if "full" in alert_size else 10))
    preferred_detail_size = max(20, self.height // 16)
    max_text_width = max(120, right - left - max(100, (right - left) // 8))
    title_size = preferred_title_size
    while title_size > 28 and _text_width(text1, title_size, True) > max_text_width:
      title_size -= 2
    detail_size = preferred_detail_size
    while text2 and detail_size > 18 and _text_width(text2, detail_size) > max_text_width:
      detail_size -= 2

    center_x = (left + right) // 2
    center_y = (top + bottom) // 2
    stroke_width = max(2, self.height // 154)
    if text2:
      _draw_stroked_text(draw, (center_x, center_y - max(18, self.height // 18)), text1,
                         title_size, True, title_color, (0, 0, 0), stroke_width, "mm")
      _draw_stroked_text(draw, (center_x, center_y + max(25, self.height // 13)), text2,
                         detail_size, False, (255, 255, 255), (0, 0, 0), stroke_width, "mm")
    else:
      _draw_stroked_text(draw, (center_x, center_y), text1, title_size, True,
                         title_color, (0, 0, 0), stroke_width, "mm")

  def _draw_driving_panel(self, image, draw, box, speed_kph, cruise_kph, enabled, scene):
    left, top, right, bottom = box
    world_top = top + int((bottom - top) * 0.47)
    world_box = (left, world_top, right, bottom)
    draw.rectangle((left, top, right, world_top), fill=(239, 241, 242))
    self._draw_road_surface(image, world_box)

    scene = scene or {}
    is_metric = bool(scene.get("is_metric", True))
    language = "en" if str(scene.get("language", "ko")).lower() == "en" else "ko"
    radar_info = int(scene.get("radar_info", 2) or 0)
    self._draw_path(image, draw, world_box, scene.get("path", []), enabled, scene)
    for point in reversed(scene.get("radar_points", [])[:10]):
      self._draw_radar_point(image, draw, world_box, point, radar_info, is_metric)
    for index, lead in reversed(list(enumerate(scene.get("leads", [])[:2]))):
      self._draw_lead(image, draw, world_box, lead, index == 0, radar_info, is_metric)
    if scene.get("left_blindspot", False):
      self._draw_blindspot_indicator(draw, world_box, "left")
    if scene.get("right_blindspot", False):
      self._draw_blindspot_indicator(draw, world_box, "right")
    self._draw_ego_vehicle(image, world_box, enabled)
    self._draw_lead_info(draw, world_box, scene.get("leads", []), is_metric, language)
    self._draw_tpms(draw, world_box, scene.get("tpms"))

    self._draw_requested_status_header(image, draw, box, speed_kph, cruise_kph, enabled, scene)
    self._draw_turn_signals(draw, world_box, scene.get("blinkers"))

  def _draw_footer(self, draw, box, footer):
    """carrot-wip style status line: address and delivered frame rate."""
    left, _, right, bottom = box
    footer = footer or {}
    size = max(11, self.height // 35)
    address = str(footer.get("ip", "") or "")
    if address:
      _draw_text(draw, (left + 34, bottom - 16), address, size, fill=(150, 168, 182), anchor="ls")
    fps = float(footer.get("fps", 0.0) or 0.0)
    if fps > 0.0:
      dot_x = left + int((right - left) * 0.115)
      dot_y = bottom - 20
      radius = max(3, size // 3)
      draw.ellipse((dot_x - radius, dot_y - radius, dot_x + radius, dot_y + radius),
                   fill=(39, 219, 139) if fps >= 4.0 else (232, 168, 62))
      _draw_text(draw, (dot_x + radius + 7, bottom - 16), "FPS %.1f Hz" % fps, size,
                 fill=(150, 168, 182), anchor="ls")

  def _draw_navi_panel(self, image, draw, box, navi, language="ko", is_metric=True):
    left, top, right, bottom = box
    panel_w = right - left
    # Preserve the complete TMap frame. The receiver requests the same wide
    # aspect ratio as this panel, so this remains edge-to-edge without crop bars.
    map_image = _safe_full_image(NAVI_MAP, (panel_w, bottom - top))
    if map_image is not None:
      # Keep the last complete TMap frame visible even if the navigation JSON
      # is briefly unavailable while its producer replaces the file.
      image.paste(map_image, (left, top))
    else:
      draw.rectangle(box, fill=(10, 17, 24))
      _draw_text(draw, ((left + right) // 2, (top + bottom) // 2), "TMAP WAIT",
                 max(20, self.height // 15), True, fill=(120, 135, 145), anchor="mm")

    # Use nMirror's native TMap artwork so the guidance hierarchy and lane
    # colors match the phone: large current turn, smaller next turn below it,
    # and the lane strip centered at the bottom.
    margin = max(8, self.height // 46)
    current = _safe_contained_image(
      NAVI_TBT_CURRENT, (int(panel_w * 0.55), int((bottom - top) * 0.42)))
    current_bottom = top + margin
    if current is not None:
      current_x = left + margin
      current_y = top + margin
      image.paste(current, (current_x, current_y), current if current.mode == "RGBA" else None)
      current_bottom = current_y + current.height

    next_turn = _safe_contained_image(
      NAVI_TBT_NEXT, (int(panel_w * 0.35), int((bottom - top) * 0.18)))
    if next_turn is not None:
      next_x = left + margin
      next_y = current_bottom + max(3, margin // 2)
      image.paste(next_turn, (next_x, next_y), next_turn if next_turn.mode == "RGBA" else None)

    lane = _safe_contained_image(
      NAVI_LANE, (int(panel_w * 0.55), int((bottom - top) * 0.18)))
    if lane is not None:
      lane_x = left + (panel_w - lane.width) // 2
      lane_y = bottom - lane.height - margin
      image.paste(lane, (lane_x, lane_y), lane if lane.mode == "RGBA" else None)

    self._draw_navi_text(draw, box, navi, language, is_metric)

  @staticmethod
  def _guidance_line(guide, is_metric, language):
    """One compact "<distance> <name>" line from a guidance_* stream."""
    if not isinstance(guide, dict):
      return ""
    distance = guide.get("distance_m")
    name = str(guide.get("main_text") or guide.get("road_name") or "")
    parts = []
    if distance is not None and float(distance) > 0:
      parts.append(_distance_text(float(distance), is_metric, language))
    if name:
      parts.append(name)
    return "  ".join(parts)

  def _draw_navi_text(self, draw, box, navi, language, is_metric):
    """Live guidance text straight from the JSON streams.

    The TMap PNG overlays carry their own distance, but the phone only sends
    them at 1 fps. These lines come from the JSON streams, so they stay current
    at the HUD frame rate. Three text draws total, roughly 0.7 ms.
    """
    left, top, right, bottom = box
    size = max(16, self.height // 22)
    small = max(13, self.height // 30)
    pad = max(6, self.height // 70)
    margin = max(8, self.height // 46)

    def plate(x, y, text, text_size, fill):
      width = int(len(text) * text_size * 0.62) + pad * 2
      draw.rounded_rectangle((x, y - pad, x + width, y + text_size + pad), radius=8,
                             fill=(8, 14, 20))
      _draw_text(draw, (x + pad, y), text, text_size, True, fill=fill, anchor="la")

    route = navi.get("route") or {}
    remain_m = route.get("remain_distance_m")
    remain_s = route.get("remain_time_sec")
    summary = []
    if remain_m is not None and float(remain_m) > 0:
      summary.append(_distance_text(float(remain_m), is_metric, language))
    if remain_s is not None and float(remain_s) > 0:
      remain_s = int(remain_s)
      summary.append(time.strftime("%H:%M", time.localtime(time.time() + remain_s)))
      minutes = max(1, remain_s // 60)
      summary.append("%d분" % minutes if language == "ko" else "%d min" % minutes)
    if summary:
      self._navi_text_cache["summary"] = summary
    else:
      summary = self._navi_text_cache.get("summary") or []
    if summary:
      text = "  ·  ".join(summary)
      width = int(len(text) * small * 0.62) + pad * 2
      plate(right - width - margin, top + margin, text, small, (196, 220, 236))

    current = self._guidance_line(navi.get("guidance_current"), is_metric, language)
    next_line = self._guidance_line(navi.get("guidance_next"), is_metric, language)
    # An atomic JSON replacement must not blink the guidance lines, exactly as
    # the native TMap overlays hold their last complete frame.
    if navi.get("guidance_current") is not None or navi.get("route") is not None:
      self._navi_text_cache["current"] = current
      self._navi_text_cache["next"] = next_line
    else:
      current = self._navi_text_cache.get("current", "")
      next_line = self._navi_text_cache.get("next", "")
    y = bottom - margin - size - pad
    if next_line:
      y -= small + pad * 3
    if current:
      plate(left + margin, y, current, size, (255, 214, 78))
      y += size + pad * 3
    if next_line:
      plate(left + margin, y, next_line, small, (176, 196, 210))

  def _theme_colors(self, theme):
    # carrot-wip compatible mapping: 0 auto by local time, 1 dark, 2 light.
    theme = int(theme or 0)
    light = theme == 2 or (theme == 0 and 6 <= time.localtime().tm_hour < 18)
    if light:
      return {"bg": (235, 239, 243), "card": (250, 251, 252), "line": (180, 188, 196),
              "primary": (25, 31, 38), "secondary": (90, 102, 114), "accent": (32, 123, 214)}
    return {"bg": (7, 12, 18), "card": (16, 23, 32), "line": (55, 68, 80),
            "primary": (235, 240, 245), "secondary": (145, 158, 168), "accent": (64, 181, 255)}

  def _draw_system_panel(self, draw, box, system, theme=0):
    colors = self._theme_colors(theme)
    left, top, right, bottom = box
    panel_w = max(1, right - left)
    panel_h = max(1, bottom - top)
    draw.rectangle(box, fill=colors["bg"])
    title_size = max(20, min(self.height // 15, panel_w // 8))
    _draw_text(draw, ((left + right) // 2, top + 28), "SYSTEM", title_size, True,
               fill=colors["primary"], anchor="mm")
    metrics = (("CPU", float(system.get("cpu", 0.0) or 0.0), "%"),
               ("TEMP", float(system.get("temp", 0.0) or 0.0), " C"),
               ("MEM", float(system.get("memory", 0.0) or 0.0), "%"),
               ("DISK", float(system.get("disk", 0.0) or 0.0), "%"))
    cores = system.get("cores") or []
    margin = max(10, panel_w // 32)
    gap = max(5, panel_h // 70)
    footer_h = max(24, panel_h // 16) if cores else margin
    cards_top = top + max(52, panel_h // 8)
    cards_bottom = bottom - footer_h
    card_h = max(50, (cards_bottom - cards_top - gap * (len(metrics) - 1)) // len(metrics))
    label_size = max(14, min(self.height // 27, panel_w // 18))
    preferred_value_size = max(22, min(self.height // 14, panel_w // 9))
    for i, (label, value, unit) in enumerate(metrics):
      x0, x1 = left + margin, right - margin
      y0 = cards_top + i * (card_h + gap)
      y1 = min(cards_bottom, y0 + card_h)
      draw.rounded_rectangle((x0, y0, x1, y1), radius=max(8, card_h // 8),
                             fill=colors["card"], outline=colors["line"], width=2)
      _draw_text(draw, (x0 + 12, (y0 + y1) // 2), label, label_size, True,
                 fill=colors["secondary"], anchor="lm")
      text = ("%.0f" % value) + unit
      value_size = preferred_value_size
      value_width = max(60, (x1 - x0) * 3 // 5)
      while value_size > 16 and _text_width(text, value_size, True) > value_width:
        value_size -= 2
      _draw_text(draw, (x1 - 12, (y0 + y1) // 2), text,
                 value_size, True, fill=colors["primary"], anchor="rm")
    if cores:
      core_text = "  ".join("C%d %.0f%%" % (i, float(v)) for i, v in enumerate(cores))
      core_size = max(10, min(self.height // 30, panel_w // 24))
      while core_size > 9 and _text_width(core_text, core_size) > panel_w - margin * 2:
        core_size -= 1
      _draw_text(draw, ((left + right) // 2, bottom - 24), core_text,
                 core_size, fill=colors["secondary"], anchor="ms")

  def _draw_debug_panel(self, draw, box, speed_kph, cruise_kph, scene, theme=0):
    colors = self._theme_colors(theme)
    left, top, right, bottom = box
    panel_w = max(1, right - left)
    draw.rectangle(box, fill=colors["bg"])
    title_size = max(22, min(self.height // 12, panel_w // 12))
    _draw_text(draw, ((left + right) // 2, top + 38), "LIVE DEBUG", title_size, True,
               fill=colors["primary"], anchor="mm")
    lead_count = len(scene.get("leads", []))
    is_metric = bool(scene.get("is_metric", True))
    speed_unit = "km/h" if is_metric else "mph"
    display_speed = _speed_value(speed_kph, is_metric)
    display_cruise = _speed_value(cruise_kph, is_metric)
    rows = (
      ("SPEED", "%.0f %s" % (display_speed, speed_unit)),
      ("CRUISE", "--" if cruise_kph <= 0 or cruise_kph >= 255 else "%.0f %s" % (display_cruise, speed_unit)),
      ("MODEL", "%d lanes / %d edges" % (len(scene.get("lanes", [])), len(scene.get("edges", [])))),
      ("RADAR", "%d lead%s" % (lead_count, "" if lead_count == 1 else "s")),
      ("NAVI", "LIVE" if scene.get("navi_live") else "WAIT"),
    )
    row_h = max(48, (bottom - top - 82) // len(rows))
    label_size = max(14, min(self.height // 26, panel_w // 28))
    preferred_value_size = max(16, min(self.height // 22, panel_w // 22))
    for index, (label, value) in enumerate(rows):
      y = top + 68 + index * row_h
      draw.line((left + 22, y + row_h - 2, right - 22, y + row_h - 2), fill=colors["line"], width=1)
      _draw_text(draw, (left + 24, y + row_h // 2), label, label_size, True,
                 fill=colors["secondary"], anchor="lm")
      value_size = preferred_value_size
      while value_size > 14 and _text_width(value, value_size, True) > panel_w * 3 // 5:
        value_size -= 1
      _draw_text(draw, (right - 24, y + row_h // 2), value, value_size, True,
                 fill=colors["primary"], anchor="rm")

  def _draw_trip_report(self, draw, box, report, theme=0, language="ko", is_metric=True):
    colors = self._theme_colors(theme)
    left, top, right, bottom = box
    draw.rectangle(box, fill=colors["bg"])
    title_size = max(24, self.height // 12)
    body_size = max(19, self.height // 18)
    title = "주행 리포트" if language == "ko" else "DRIVING REPORT"
    _draw_text(draw, ((left + right) // 2, top + 42), title, title_size, True,
               fill=colors["primary"], anchor="mm")
    duration_s = max(0.0, float(report.get("duration_s", 0.0) or 0.0))
    distance_m = max(0.0, float(report.get("distance_m", 0.0) or 0.0))
    average_speed = _speed_value(float(report.get("average_speed_kph", 0.0) or 0.0), is_metric)
    max_speed = _speed_value(float(report.get("max_speed_kph", 0.0) or 0.0), is_metric)
    speed_unit = "km/h" if is_metric else "mph"
    engaged_time_s = max(0.0, float(report.get("engaged_time_s", 0.0) or 0.0))
    engaged_ratio = 100.0 * engaged_time_s / duration_s if duration_s > 0.0 else 0.0
    labels = {
      "time": "시간" if language == "ko" else "TIME",
      "distance": "거리" if language == "ko" else "DIST",
      "average": "평균" if language == "ko" else "AVG",
      "maximum": "최고" if language == "ko" else "MAX",
      "engaged": "OP 사용" if language == "ko" else "OP TIME",
      "accel": "가감속" if language == "ko" else "ACC/DEC",
    }
    rows = (
      (labels["time"], "%02d:%02d" % (int(duration_s) // 3600, (int(duration_s) // 60) % 60)),
      (labels["distance"], _distance_text(distance_m, is_metric, language)),
      (labels["average"], "%.0f %s" % (average_speed, speed_unit)),
      (labels["maximum"], "%.0f %s" % (max_speed, speed_unit)),
      (labels["engaged"], "%.0f%%" % engaged_ratio),
      (labels["accel"], "%+.1f/%.1f  H%d/%d" % (float(report.get("max_accel", 0.0) or 0.0),
                                                  float(report.get("max_decel", 0.0) or 0.0),
                                                  int(report.get("hard_accel_count", 0) or 0),
                                                  int(report.get("hard_brake_count", 0) or 0))),
    )
    card_left, card_right = left + 24, right - 24
    row_h = max(58, (bottom - top - 92) // len(rows))
    for index, (label, value) in enumerate(rows):
      row_top = top + 72 + index * row_h
      draw.rounded_rectangle((card_left, row_top, card_right, row_top + row_h - 8), radius=12,
                             fill=colors["card"], outline=colors["line"], width=2)
      _draw_text(draw, (card_left + 18, row_top + (row_h - 8) // 2), label, body_size, True,
                 fill=colors["secondary"], anchor="lm")
      _draw_text(draw, (card_right - 18, row_top + (row_h - 8) // 2), value, body_size, True,
                 fill=colors["primary"], anchor="rm")

  def render(self, speed_kph, cruise_kph, enabled, navi=None, scene=None):
    navi = navi or {}
    scene = self._stabilize_scene_geometry(scene or {})
    image = Image.new("RGB", (self.width, self.height), (5, 8, 12))
    draw = ImageDraw.Draw(image)
    # Fixed 4:2:4 layout. Right panel: 1 auto, 2 live debug, 3 trip report.
    screen_mode = int(scene.get("screen_mode", 1) or 1)
    theme = int(scene.get("theme", 0) or 0)
    language = "en" if str(scene.get("language", "ko")).lower() == "en" else "ko"
    is_metric = bool(scene.get("is_metric", True))
    monotonic_now = time.monotonic()
    if _navi_route_active(navi):
      self._route_visible_until = monotonic_now + NAVI_ROUTE_GRACE_S
    route_visible = monotonic_now < self._route_visible_until

    drive_split = int(self.width * self.DRIVE_RATIO)
    system_split = int(self.width * (self.DRIVE_RATIO + self.SYSTEM_RATIO))
    driving_box = (0, 0, drive_split - 3, self.height)
    system_box = (drive_split + 3, 0, system_split - 3, self.height)
    right_box = (system_split + 3, 0, self.width, self.height)

    self._draw_driving_panel(image, draw, driving_box,
                             speed_kph, cruise_kph, enabled, scene)
    self._draw_system_panel(draw, system_box, scene.get("system") or {}, theme)
    draw.rectangle((drive_split - 3, 0, drive_split + 3, self.height), fill=(34, 42, 50))
    draw.rectangle((system_split - 3, 0, system_split + 3, self.height), fill=(34, 42, 50))

    if screen_mode == 2:
      self._draw_debug_panel(draw, right_box, speed_kph, cruise_kph, scene, theme)
    elif screen_mode == 3 or not route_visible:
      # Auto mode shows navigation only for a live destination. A two-second
      # grace period above absorbs atomic JSON replacement without map/report flicker.
      self._draw_trip_report(draw, right_box, scene.get("trip_report") or {},
                             theme, language, is_metric)
    else:
      self._draw_navi_panel(image, draw, right_box, navi, language, is_metric)
    self._draw_alert(draw, scene.get("alert"), driving_box)
    return image

  def encode_portrait_jpeg(self, image):
    if self.mirror:
      transpose = getattr(Image, "Transpose", Image)
      image = image.transpose(transpose.FLIP_LEFT_RIGHT)
    portrait = image.transpose(Image.ROTATE_90)
    output = io.BytesIO()
    portrait.save(output, format="JPEG", quality=self.jpeg_quality, optimize=False,
                  progressive=False, subsampling=2)
    return output.getvalue()
