import io
import json
import math
import os
import time
from collections import OrderedDict, deque

from PIL import Image, ImageDraw, ImageFont


NAVI_STATE = "/dev/shm/carrot_navi_route.json"
NAVI_MAP = "/dev/shm/carrot_navi_map.jpg"
NAVI_LANE = "/dev/shm/carrot_navi_lane_bottom.png"
NAVI_MAX_AGE_MS = 35000
BITMAP_FONT_DATA = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", "Pretendard-SemiBold.fnt")
BITMAP_FONT_IMAGE = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", "Pretendard-SemiBold.png")
HANGUL_BITMAP_FONT_DATA = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", "Pretendard-Hangul.fnt")
HANGUL_BITMAP_FONT_IMAGE = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts", "Pretendard-Hangul.png")
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


def _safe_image(path):
  try:
    stat = os.stat(path)
    signature = (stat.st_mtime, stat.st_size)
    cached = _IMAGE_CACHE.get(path)
    if cached is not None and cached[0] == signature:
      return cached[1]
    with Image.open(path) as source:
      image = source.convert("RGB")
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


def _safe_fitted_image(path, size):
  image = _safe_image(path)
  if image is None:
    return None
  signature = _IMAGE_CACHE[path][0]
  key = (path, signature, int(size[0]), int(size[1]), "cover")
  cached = _FIT_CACHE.get(key)
  if cached is not None:
    return cached
  fitted = _fit_cover(image, size)
  _FIT_CACHE[key] = fitted
  return fitted


def _safe_full_image(path, size):
  """Resize the complete source frame without cropping any map edge."""
  image = _safe_image(path)
  if image is None:
    return None
  signature = _IMAGE_CACHE[path][0]
  key = (path, signature, int(size[0]), int(size[1]), "full")
  cached = _FIT_CACHE.get(key)
  if cached is not None:
    return cached
  resampling = getattr(Image, "Resampling", Image)
  fitted = image.resize((max(1, int(size[0])), max(1, int(size[1]))), resampling.BILINEAR)
  _FIT_CACHE[key] = fitted
  return fitted


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
  DRIVE_RATIO = 0.60
  MAX_DISTANCE_M = 120.0

  def __init__(self, width, height, jpeg_quality=58):
    self.width = int(width)
    self.height = int(height)
    self.jpeg_quality = 58
    self.set_jpeg_quality(jpeg_quality)
    self.mirror = False
    self.graph_speed = deque(maxlen=180)
    self.graph_cpu = deque(maxlen=180)
    self.graph_temp = deque(maxlen=180)
    # Cache the static carrot-style road surface per panel size.
    self._road_backgrounds = {}

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
    """Layered perspective road inspired by the full carrot raylib scene."""
    left, top, right, bottom = panel
    size = (max(1, right - left), max(1, bottom - top))
    background = self._road_backgrounds.get(size)
    if background is None:
      width, height = size
      background = Image.new("RGB", size, (4, 8, 15))
      road = ImageDraw.Draw(background)
      horizon = int(height * 0.105)
      for band in range(12):
        y0 = int(horizon * band / 12.0)
        y1 = int(horizon * (band + 1) / 12.0) + 1
        mix = band / 11.0
        road.rectangle((0, y0, width, y1),
                       fill=(int(5 + 5 * mix), int(10 + 10 * mix), int(20 + 18 * mix)))
      center = width * 0.5
      far_half = max(8, width * 0.058)
      near_half = width * 0.44
      road.polygon(((center - far_half, horizon), (center + far_half, horizon),
                    (center + near_half, height), (center - near_half, height)),
                   fill=(13, 20, 30))
      for band in range(9):
        near_t = band / 9.0
        far_t = (band + 1) / 9.0
        y0 = int(horizon + (height - horizon) * near_t)
        y1 = int(horizon + (height - horizon) * far_t) + 1
        half0 = far_half + (near_half - far_half) * near_t
        half1 = far_half + (near_half - far_half) * far_t
        shade = 16 + (band % 2) * 3 + int(band * 0.7)
        road.polygon(((center - half0, y0), (center + half0, y0),
                      (center + half1, y1), (center - half1, y1)),
                     fill=(shade, shade + 7, shade + 15))
      shoulder = (42, 55, 70)
      road.line(((center - far_half, horizon), (center - near_half, height)),
                fill=shoulder, width=max(2, height // 95))
      road.line(((center + far_half, horizon), (center + near_half, height)),
                fill=shoulder, width=max(2, height // 95))
      road.line((int(center - far_half * 1.8), horizon, int(center + far_half * 1.8), horizon),
                fill=(46, 67, 91), width=max(1, height // 150))
      self._road_backgrounds[size] = background
    image.paste(background, (left, top))

  def _draw_lane_marking(self, draw, panel, points, color, probability):
    probability = _clamp(float(probability), 0.0, 1.0)
    filtered = [(point, self._project(panel, point[0], point[1]))
                for point in points if 0.0 <= point[0] <= self.MAX_DISTANCE_M]
    for index in range(len(filtered) - 1):
      (world_a, screen_a), (world_b, screen_b) = filtered[index:index + 2]
      distance = max(0.0, (world_a[0] + world_b[0]) * 0.5)
      perspective = math.pow(max(0.0, 1.0 - distance / self.MAX_DISTANCE_M), 1.1)
      core_width = max(1, int(1 + (3 + 4 * probability) * perspective))
      glow_width = core_width + max(2, self.height // 90)
      glow = tuple(max(0, int(channel * 0.30)) for channel in color)
      draw.line((screen_a, screen_b), fill=glow, width=glow_width)
      draw.line((screen_a, screen_b), fill=color, width=core_width)

  @staticmethod
  def _path_color(enabled, scene):
    if not scene.get("show_path_status_color", True):
      # QColor::fromHslF(197 / 360., 1.0, 0.55) used by the EON UI.
      return (26, 190, 255)
    if not enabled:
      return (0, 0, 0)
    if scene.get("leads"):
      accel = float(scene.get("accel", 0.0) or 0.0)
      if accel >= 0.5:
        return (255, 153, 0)
      if accel <= -0.5:
        return (255, 0, 0)
      return (255, 255, 0)
    return (0, 153, 0)

  def _draw_path(self, image, draw, panel, points, enabled=False, scene=None):
    scene = scene or {}
    if len(points) < 2:
      points = [(0.0, 0.0), (12.0, 0.0), (30.0, 0.0), (60.0, 0.0), (100.0, 0.0)]
    left_edge, right_edge, center_line = [], [], []
    for longitudinal, lateral in points:
      if 0.0 <= longitudinal <= self.MAX_DISTANCE_M:
        near_taper = _clamp(longitudinal / 4.0, 0.0, 1.0)
        half_width = (0.82 + 0.22 * min(1.0, longitudinal / 60.0)) * near_taper
        left_edge.append(self._project(panel, longitudinal, lateral + half_width))
        right_edge.append(self._project(panel, longitudinal, lateral - half_width))
        center_line.append(self._project(panel, longitudinal, lateral))
    polygon = left_edge + list(reversed(right_edge))
    if len(polygon) < 4:
      return
    color = self._path_color(enabled, scene)
    left, top, right, bottom = panel
    overlay = Image.new("RGBA", (right - left, bottom - top), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.polygon([(x - left, y - top) for x, y in polygon],
                         fill=(color[0], color[1], color[2], 92 if enabled else 65))
    image.paste(overlay, (left, top), overlay)
    edge_glow = tuple(max(25, int(channel * 0.38)) for channel in color)
    edge_width = max(2, self.height // 92)
    for edge in (left_edge, right_edge):
      if len(edge) >= 2:
        draw.line(edge, fill=edge_glow, width=edge_width + 3, joint="curve")
        draw.line(edge, fill=color, width=edge_width, joint="curve")
    if len(center_line) >= 2:
      center_color = tuple(min(255, int(channel * 0.55 + 105)) for channel in color)
      draw.line(center_line, fill=center_color, width=max(1, edge_width // 2), joint="curve")

  def _draw_lead(self, draw, panel, lead, primary, radar_info=2, is_metric=True):
    distance = float(lead.get("distance", 0.0) or 0.0)
    lateral = float(lead.get("lateral", 0.0) or 0.0)
    if distance <= 0.0 or distance > self.MAX_DISTANCE_M:
      return
    cx, cy = self._project(panel, distance, lateral)
    scale = math.pow(max(0.10, 1.0 - distance / self.MAX_DISTANCE_M), 1.10)
    car_w = int(28 + 112 * scale)
    car_h = int(18 + 66 * scale)
    color = (255, 174, 58) if primary else (89, 177, 255)
    outline_width = max(2, self.height // 120)
    shadow_w = int(car_w * 1.18)
    draw.ellipse((cx - shadow_w // 2, cy - max(3, car_h // 9),
                  cx + shadow_w // 2, cy + max(4, car_h // 8)), fill=(2, 4, 7))
    body = (cx - car_w // 2, cy - car_h, cx + car_w // 2, cy)
    draw.rounded_rectangle(body, radius=max(5, car_w // 9), fill=(24, 31, 42),
                           outline=color, width=outline_width)
    roof = (cx - int(car_w * 0.31), cy - int(car_h * 0.91),
            cx + int(car_w * 0.31), cy - int(car_h * 0.48))
    draw.rounded_rectangle(roof, radius=max(3, car_w // 14),
                           fill=(50, 68, 83), outline=(111, 139, 157), width=1)
    bumper_y = cy - max(4, car_h // 8)
    draw.line((cx - int(car_w * 0.38), bumper_y, cx + int(car_w * 0.38), bumper_y),
              fill=(112, 124, 135), width=max(1, outline_width // 2))
    lamp_r = max(2, car_w // 18)
    lamp_color = (255, 65, 55) if float(lead.get("relative_speed", 0.0) or 0.0) < -0.5 else (255, 193, 74)
    for lamp_x in (cx - int(car_w * 0.31), cx + int(car_w * 0.31)):
      draw.ellipse((lamp_x - lamp_r, cy - int(car_h * 0.28) - lamp_r,
                    lamp_x + lamp_r, cy - int(car_h * 0.28) + lamp_r), fill=lamp_color)
    if radar_info > 0:
      relative_speed = _speed_value(float(lead.get("relative_speed", 0.0) or 0.0) * 3.6, is_metric)
      speed_unit = "km/h" if is_metric else "mph"
      if radar_info in (2, 4):
        label = "%s  %+.0f %s" % (_distance_text(distance, is_metric), relative_speed, speed_unit)
      else:
        label = "%+.0f %s" % (relative_speed, speed_unit)
      _draw_text(draw, (cx, cy - car_h - max(8, self.height // 55)), label,
                 max(14, self.height // 25), True, fill=color, anchor="ms")

  def _draw_radar_point(self, draw, panel, point, radar_info=2, is_metric=True):
    distance = float(point.get("distance", 0.0) or 0.0)
    if distance <= 0.0 or distance > self.MAX_DISTANCE_M:
      return
    cx, cy = self._project(panel, distance, float(point.get("lateral", 0.0) or 0.0))
    stationary = bool(point.get("stationary", False))
    color = (255, 169, 45) if stationary else (97, 190, 255)
    radius = max(4, int(13 * (1.0 - distance / self.MAX_DISTANCE_M)) + 3)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius),
                 fill=(18, 26, 34), outline=color, width=max(1, radius // 3))
    if radar_info <= 0 or (radar_info in (1, 2) and stationary):
      return
    relative_speed = _speed_value(float(point.get("relative_speed", 0.0) or 0.0) * 3.6, is_metric)
    if radar_info in (2, 4):
      label = "%s %+.0f" % (_distance_text(distance, is_metric), relative_speed)
    else:
      label = "%+.0f" % relative_speed
    _draw_text(draw, (cx, cy - radius - 5), label, max(11, self.height // 34), True,
               fill=color, anchor="ms")

  def _draw_speed_limit(self, draw, x, y, limit):
    if limit <= 0:
      return
    radius = max(29, self.height // 10)
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(250, 250, 250),
                 outline=(220, 45, 45), width=max(6, radius // 6))
    _draw_text(draw, (x, y), str(limit), max(24, radius), True, fill=(20, 20, 20), anchor="mm")

  def _draw_driving_mode(self, draw, box, mode):
    modes = {
      1: ("ECO", (40, 210, 125)),
      2: ("SAFE", (255, 169, 45)),
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

  def _draw_tpms(self, draw, box, tpms):
    if not tpms:
      return
    values = [tpms.get(key) for key in ("fl", "fr", "rl", "rr")]
    valid = [value for value in values if value is not None and 5.0 <= float(value) <= 60.0]
    if not valid:
      return
    _, _, right, bottom = box
    center_x = right - max(88, self.width // 25)
    center_y = bottom - max(78, self.height // 6)
    car_w = max(28, self.height // 12)
    car_h = max(64, self.height // 5)
    draw.rounded_rectangle((center_x - car_w // 2, center_y - car_h // 2,
                            center_x + car_w // 2, center_y + car_h // 2),
                           radius=8, fill=(28, 35, 43), outline=(90, 105, 118), width=2)
    offsets = ((-55, -32), (55, -32), (-55, 32), (55, 32))
    for value, (dx, dy) in zip(values, offsets):
      is_valid = value is not None and 5.0 <= float(value) <= 60.0
      text = str(int(round(float(value)))) if is_valid else "--"
      color = (235, 70, 70) if is_valid and float(value) < 31.0 else (220, 228, 234)
      _draw_text(draw, (center_x + dx, center_y + dy), text, max(16, self.height // 24), True,
                 fill=color, anchor="mm")

  def _draw_alert(self, draw, alert):
    if not alert or not alert.get("text1"):
      return
    status = str(alert.get("status", "")).lower()
    critical = status in ("critical", "2") or "critical" in status
    color = (225, 55, 55) if critical else (255, 169, 45)
    height = max(105, int(self.height * 0.31))
    top = (self.height - height) // 2
    margin = max(34, self.width // 32)
    draw.rounded_rectangle((margin, top, self.width - margin, top + height), radius=22,
                           fill=(10, 14, 19), outline=color, width=max(4, self.height // 80))
    text1 = str(alert.get("text1", ""))
    text2 = str(alert.get("text2", ""))
    _draw_text(draw, (self.width // 2, top + int(height * 0.38)), text1,
               max(30, self.height // 10), True, fill=(250, 250, 250), anchor="mm")
    if text2:
      _draw_text(draw, (self.width // 2, top + int(height * 0.73)), text2,
                 max(20, self.height // 16), True, fill=(205, 215, 222), anchor="mm")

  def _draw_driving_panel(self, image, draw, box, speed_kph, cruise_kph, enabled, limit, scene):
    left, top, right, bottom = box
    self._draw_road_surface(image, box)
    horizon = top + int((bottom - top) * 0.10)

    scene = scene or {}
    for edge in scene.get("edges", []):
      probability = float(edge.get("probability", 0.5) or 0.5)
      color = (int(112 + 115 * probability), int(48 + 28 * probability),
               int(58 + 32 * probability))
      self._draw_polyline(draw, box, edge.get("points", []),
                          tuple(max(18, int(channel * 0.35)) for channel in color),
                          max(6, self.height // 55))
      self._draw_polyline(draw, box, edge.get("points", []), color,
                          max(2, self.height // 105))

    lanes = scene.get("lanes", []) or self._fallback_lanes()
    for index, lane in enumerate(lanes):
      probability = float(lane.get("probability", 0.5) or 0.5)
      intensity = int(120 + 135 * _clamp(probability, 0.0, 1.0))
      if index == 0:
        color = (173, 198, 255)
      elif index == len(lanes) - 1:
        color = (255, 154, 194)
      else:
        color = (intensity, intensity, min(255, intensity + 12))
      self._draw_lane_marking(draw, box, lane.get("points", []), color, probability)

    is_metric = bool(scene.get("is_metric", True))
    radar_info = int(scene.get("radar_info", 2) or 0)
    self._draw_path(image, draw, box, scene.get("path", []), enabled, scene)
    for point in scene.get("radar_points", []):
      self._draw_radar_point(draw, box, point, radar_info, is_metric)
    for index, lead in enumerate(scene.get("leads", [])[:2]):
      self._draw_lead(draw, box, lead, index == 0, radar_info, is_metric)

    status_color = (40, 210, 125) if enabled else (115, 125, 135)
    speed_y = bottom - int((bottom - top) * 0.08)
    display_speed = _speed_value(speed_kph, is_metric)
    display_cruise = _speed_value(cruise_kph, is_metric)
    display_limit = _speed_value(limit, is_metric)
    _draw_text(draw, (left + 34, speed_y), str(max(0, int(round(display_speed)))),
               max(58, int(self.height * 0.25)), True, fill=(245, 248, 250), anchor="ls")
    _draw_text(draw, (left + int((right - left) * 0.24), speed_y - 4), "km/h" if is_metric else "mph",
               max(17, self.height // 22), fill=(145, 158, 168), anchor="ls")
    cruise = "--" if cruise_kph <= 0 or cruise_kph >= 255 else str(int(round(display_cruise)))
    _draw_text(draw, (left + int((right - left) * 0.24), speed_y - max(34, self.height // 9)), "SET " + cruise,
               max(22, self.height // 13), True, fill=status_color, anchor="ls")
    self._draw_speed_limit(draw, left + 70, top + 72, int(round(display_limit)))
    self._draw_driving_mode(draw, box, int(scene.get("driving_mode", 0) or 0))
    self._draw_energy_mode(draw, box, scene.get("energy_mode"))
    self._draw_tpms(draw, box, scene.get("tpms"))
    _draw_text(draw, (right - 22, top + 24), "CARROT HUD", max(13, self.height // 30),
               fill=(96, 116, 132), anchor="ra")

  def _draw_navi_panel(self, image, draw, box, navi, language="ko", is_metric=True):
    left, top, right, bottom = box
    panel_w = right - left
    # Preserve the complete TMap frame. Cover-cropping removed the source
    # frame's right and bottom edges when its aspect ratio differed from the
    # 40-percent cluster panel.
    map_image = _safe_full_image(NAVI_MAP, (panel_w, bottom - top))
    if map_image is not None and navi:
      image.paste(map_image, (left, top))
    else:
      draw.rectangle(box, fill=(10, 17, 24))
      _draw_text(draw, ((left + right) // 2, (top + bottom) // 2), "TMAP WAIT",
                 max(20, self.height // 15), True, fill=(120, 135, 145), anchor="mm")

    if not navi:
      return
    guide = navi.get("guidance_current") or {}
    route = navi.get("route") or {}
    instruction = guide.get("main_text") or guide.get("road_name") or ("안내 없음" if language == "ko" else "No guidance")
    distance = int(guide.get("distance_m", -1) or -1)
    card = (left + 18, top + 18, right - 18, top + int(self.height * 0.31))
    draw.rounded_rectangle(card, radius=18, fill=(12, 18, 25), outline=(52, 151, 108), width=3)
    if len(instruction) > 17:
      instruction = instruction[:16] + "…"
    _draw_text(draw, (card[0] + 20, card[1] + 18), instruction, max(24, self.height // 12), True,
               fill=(240, 242, 244), anchor="la")
    if distance >= 0:
      distance_text = _distance_text(distance, is_metric, language)
      _draw_text(draw, (card[0] + 20, card[3] - 18), distance_text,
                 max(23, self.height // 11), True, fill=(64, 181, 255), anchor="ls")

    lane_w, lane_h = int(panel_w * 0.58), int(self.height * 0.18)
    lane = _safe_fitted_image(NAVI_LANE, (lane_w, lane_h))
    if lane is not None:
      lane_x = right - lane_w - 18
      lane_y = bottom - lane_h - 18
      image.paste(lane, (lane_x, lane_y))

    remain_distance = int(route.get("remain_distance_m", -1) or -1)
    if remain_distance >= 0:
      remain = _distance_text(remain_distance, is_metric, language, prefix=True)
      draw.rounded_rectangle((left + 18, bottom - 62, left + min(panel_w - 18, 330), bottom - 18),
                             radius=12, fill=(12, 18, 25))
      _draw_text(draw, (left + 32, bottom - 39), remain, max(16, self.height // 20), True,
                 fill=(205, 215, 222), anchor="lm")

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
    draw.rectangle(box, fill=colors["bg"])
    _draw_text(draw, ((left + right) // 2, top + 38), "SYSTEM", max(24, self.height // 12), True,
               fill=colors["primary"], anchor="mm")
    metrics = (("CPU", float(system.get("cpu", 0.0) or 0.0), "%"),
               ("TEMP", float(system.get("temp", 0.0) or 0.0), " C"),
               ("MEM", float(system.get("memory", 0.0) or 0.0), "%"),
               ("DISK", float(system.get("disk", 0.0) or 0.0), "%"))
    card_w = max(1, (right - left - 66) // 2)
    card_h = max(62, (bottom - top - 150) // 2)
    for i, (label, value, unit) in enumerate(metrics):
      col, row = i % 2, i // 2
      x0 = left + 22 + col * (card_w + 22)
      y0 = top + 72 + row * (card_h + 18)
      x1, y1 = x0 + card_w, min(bottom - 18, y0 + card_h)
      draw.rounded_rectangle((x0, y0, x1, y1), radius=14, fill=colors["card"], outline=colors["line"], width=2)
      _draw_text(draw, (x0 + 14, y0 + 16), label, max(16, self.height // 24), True,
                 fill=colors["secondary"], anchor="la")
      text = ("%.0f" % value) + unit
      _draw_text(draw, ((x0 + x1) // 2, (y0 + y1) // 2 + 10), text,
                 max(26, self.height // 10), True, fill=colors["primary"], anchor="mm")
    cores = system.get("cores") or []
    if cores:
      core_text = "  ".join("C%d %.0f%%" % (i, float(v)) for i, v in enumerate(cores))
      _draw_text(draw, ((left + right) // 2, bottom - 24), core_text,
                 max(12, self.height // 30), fill=colors["secondary"], anchor="ms")

  def _draw_debug_panel(self, draw, box, speed_kph, cruise_kph, scene, theme=0):
    colors = self._theme_colors(theme)
    left, top, right, bottom = box
    draw.rectangle(box, fill=colors["bg"])
    _draw_text(draw, ((left + right) // 2, top + 38), "LIVE DEBUG", max(24, self.height // 12), True,
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
    for index, (label, value) in enumerate(rows):
      y = top + 68 + index * row_h
      draw.line((left + 22, y + row_h - 2, right - 22, y + row_h - 2), fill=colors["line"], width=1)
      _draw_text(draw, (left + 24, y + row_h // 2), label, max(15, self.height // 26), True,
                 fill=colors["secondary"], anchor="lm")
      _draw_text(draw, (right - 24, y + row_h // 2), value, max(17, self.height // 22), True,
                 fill=colors["primary"], anchor="rm")

  def _update_graph_history(self, speed_kph, system):
    self.graph_speed.append(max(0.0, min(200.0, float(speed_kph))))
    self.graph_cpu.append(max(0.0, min(100.0, float(system.get("cpu", 0.0) or 0.0))))
    self.graph_temp.append(max(0.0, min(100.0, float(system.get("temp", 0.0) or 0.0))))

  def _draw_graph_panel(self, draw, box, theme=0, title="LIVE GRAPH"):
    colors = self._theme_colors(theme)
    left, top, right, bottom = box
    draw.rectangle(box, fill=colors["bg"])
    _draw_text(draw, ((left + right) // 2, top + 34), title, max(22, self.height // 13), True,
               fill=colors["primary"], anchor="mm")
    graph = (left + 26, top + 68, right - 26, bottom - 34)
    draw.rounded_rectangle(graph, radius=12, fill=colors["card"], outline=colors["line"], width=2)
    for fraction in (0.25, 0.5, 0.75):
      y = int(graph[1] + (graph[3] - graph[1]) * fraction)
      draw.line((graph[0], y, graph[2], y), fill=colors["line"], width=1)
    series = (
      (self.graph_speed, 200.0, (64, 181, 255), "SPD"),
      (self.graph_cpu, 100.0, (40, 210, 125), "CPU"),
      (self.graph_temp, 100.0, (255, 169, 45), "TMP"),
    )
    for series_index, (values, maximum, color, label) in enumerate(series):
      values = list(values)
      if len(values) >= 2:
        step = float(graph[2] - graph[0]) / max(1, len(values) - 1)
        points = [(int(graph[0] + index * step),
                   int(graph[3] - (graph[3] - graph[1]) * value / maximum))
                  for index, value in enumerate(values)]
        draw.line(points, fill=color, width=max(2, self.height // 150))
      _draw_text(draw, (left + 22 + series_index * 90, top + 25), label,
                 max(12, self.height // 32), True, fill=color, anchor="lm")

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
    scene = scene or {}
    image = Image.new("RGB", (self.width, self.height), (5, 8, 12))
    draw = ImageDraw.Draw(image)
    screen_mode = int(scene.get("screen_mode", 0) or 0)
    theme = int(scene.get("theme", 0) or 0)
    language = "en" if str(scene.get("language", "ko")).lower() == "en" else "ko"
    is_metric = bool(scene.get("is_metric", True))
    self._update_graph_history(speed_kph, scene.get("system") or {})
    if screen_mode == 3:
      self._draw_graph_panel(draw, (0, 0, self.width, self.height), theme, "FULL LIVE GRAPH")
      self._draw_alert(draw, scene.get("alert"))
      return image
    divider = int(self.width * self.DRIVE_RATIO)
    speed_state = navi.get("speed") or {}
    limit = int(speed_state.get("road_limit_kph", 0) or 0)
    panel_layout = int(scene.get("panel_layout", 0) or 0)
    driving_box = (0, 0, divider - 3, self.height)
    info_box = (divider + 3, 0, self.width, self.height)
    if panel_layout == 1:
      info_width = self.width - divider
      info_box = (0, 0, info_width - 3, self.height)
      driving_box = (info_width + 3, 0, self.width, self.height)
    self._draw_driving_panel(image, draw, driving_box,
                             speed_kph, cruise_kph, enabled, limit, scene)
    if panel_layout == 1:
      split = self.width - divider
      draw.rectangle((split - 3, 0, split + 3, self.height), fill=(34, 42, 50))
    else:
      draw.rectangle((divider - 3, 0, divider + 3, self.height), fill=(34, 42, 50))
    if screen_mode == 1:
      self._draw_debug_panel(draw, info_box, speed_kph, cruise_kph, scene, theme)
    elif screen_mode == 2:
      self._draw_system_panel(draw, info_box, scene.get("system") or {}, theme)
    elif screen_mode == 4:
      self._draw_graph_panel(draw, info_box, theme)
    elif screen_mode == 5:
      self._draw_trip_report(draw, info_box, scene.get("trip_report") or {}, theme, language, is_metric)
    elif not navi and scene.get("trip_report"):
      # carrot-wip mode 0 behavior: live navigation when available, otherwise driving report.
      self._draw_trip_report(draw, info_box, scene["trip_report"], theme, language, is_metric)
    else:
      self._draw_navi_panel(image, draw, info_box, navi, language, is_metric)
    self._draw_alert(draw, scene.get("alert"))
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
