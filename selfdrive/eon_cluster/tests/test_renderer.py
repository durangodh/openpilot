import json
import time

import selfdrive.eon_cluster.renderer as renderer_module
from selfdrive.eon_cluster.renderer import HudRenderer, read_navi_state


def test_stale_navi_state_is_rejected(tmp_path):
  path = tmp_path / "state.json"
  path.write_text(json.dumps({"updated_at_ms": int(time.time() * 1000) - 40000}))
  assert read_navi_state(str(path)) == {}


def test_fresh_navi_state_is_loaded(tmp_path):
  path = tmp_path / "state.json"
  path.write_text(json.dumps({"updated_at_ms": int(time.time() * 1000), "route": {"remain_distance_m": 1000}}))
  assert read_navi_state(str(path))["route"]["remain_distance_m"] == 1000


def test_missing_imagingft_uses_default_font(monkeypatch):
  renderer_module._FONT_CACHE.clear()
  fallback_font = object()

  def missing_freetype(*_args, **_kwargs):
    raise ImportError("The _imagingft C module is not installed")

  monkeypatch.setattr(renderer_module.ImageFont, "truetype", missing_freetype)
  monkeypatch.setattr(renderer_module.ImageFont, "load_default", lambda: fallback_font)
  assert renderer_module._font(42, True) is fallback_font
  renderer_module._FONT_CACHE.clear()


def test_default_font_can_fall_back_without_freetype(monkeypatch):
  renderer_module._FONT_CACHE.clear()
  fallback_font = object()

  def missing_freetype(*_args, **_kwargs):
    raise ImportError("The _imagingft C module is not installed")

  monkeypatch.setattr(renderer_module.ImageFont, "truetype", missing_freetype)
  monkeypatch.setattr(renderer_module.ImageFont, "load_default", missing_freetype)
  monkeypatch.setattr(renderer_module.ImageFont, "load_default_imagefont", lambda: fallback_font, raising=False)
  assert renderer_module._font(42) is fallback_font
  renderer_module._FONT_CACHE.clear()


def test_bitmap_atlas_fallback_scales_without_freetype(monkeypatch):
  fallback_font = renderer_module.ImageFont.load_default()

  def missing_freetype(*_args, **_kwargs):
    raise ImportError("The _imagingft C module is not installed")

  renderer_module._FONT_CACHE.clear()
  renderer_module._BITMAP_FONT_KEYS.clear()
  renderer_module._BITMAP_TEXT_CACHE.clear()
  renderer_module._HANGUL_BITMAP_ATLAS = None
  renderer_module._HANGUL_BITMAP_ATLAS_LOAD_ATTEMPTED = False
  monkeypatch.setattr(renderer_module.ImageFont, "truetype", missing_freetype)
  monkeypatch.setattr(renderer_module.ImageFont, "load_default", lambda: fallback_font)

  from PIL import Image, ImageDraw
  image = Image.new("RGB", (400, 160), (0, 0, 0))
  renderer_module._draw_text(ImageDraw.Draw(image), (200, 80), "CPU 62%", 64, True,
                             fill=(255, 255, 255), anchor="mm")
  bounds = image.getbbox()
  assert bounds is not None
  assert bounds[3] - bounds[1] >= 35
  assert renderer_module._load_bitmap_atlas() is not None
  hangul_atlas = renderer_module._load_hangul_bitmap_atlas()
  assert hangul_atlas is not None
  assert ord("안") in hangul_atlas[1]
  hangul_image, hangul_glyphs, _ = hangul_atlas
  for character in "동탄남은거리":
    glyph = hangul_glyphs[ord(character)]
    crop = hangul_image.crop((glyph["x"], glyph["y"],
                              glyph["x"] + glyph["width"], glyph["y"] + glyph["height"]))
    assert all(crop.getpixel((x, 0)) == 0 for x in range(crop.width))
    assert all(crop.getpixel((x, crop.height - 1)) == 0 for x in range(crop.width))
  hangul_mask = renderer_module._atlas_text_mask("안내", 40)
  fallback_mask = renderer_module._atlas_text_mask("??", 40)
  assert hangul_mask is not None
  assert hangul_mask.tobytes() != fallback_mask.tobytes()
  renderer_module._FONT_CACHE.clear()
  renderer_module._BITMAP_FONT_KEYS.clear()
  renderer_module._BITMAP_TEXT_CACHE.clear()


def test_path_tapers_without_bottom_edge_bar():
  from PIL import Image, ImageDraw
  renderer = HudRenderer(1920, 462, 50)
  image = Image.new("RGB", (1150, 462), (0, 0, 0))
  renderer._draw_path(image, ImageDraw.Draw(image), (0, 0, 1150, 462),
                      [(0.0, 0.0), (10.0, 0.0), (30.0, 0.0), (80.0, 0.0)],
                      True, {"show_path_status_color": False})
  assert sum(image.getpixel((x, 461)) == (26, 190, 255) for x in range(450, 700)) < 10


def test_path_status_colors_match_eon_ui():
  color = HudRenderer._path_color
  assert color(False, {"show_path_status_color": True}) == (0, 0, 0)
  assert color(True, {"show_path_status_color": True, "leads": []}) == (0, 153, 0)
  assert color(True, {"show_path_status_color": True, "leads": [{}], "accel": 0.0}) == (255, 255, 0)
  assert color(True, {"show_path_status_color": True, "leads": [{}], "accel": 0.5}) == (255, 153, 0)
  assert color(True, {"show_path_status_color": True, "leads": [{}], "accel": -0.5}) == (255, 0, 0)
  assert color(True, {"show_path_status_color": False}) == (26, 190, 255)


def test_portrait_jpeg_geometry():
  renderer = HudRenderer(1920, 462, 50)
  frame = renderer.render(72.0, 90.0, True, {})
  jpeg = renderer.encode_portrait_jpeg(frame)
  from PIL import Image
  import io
  with Image.open(io.BytesIO(jpeg)) as image:
    assert image.size == (462, 1920)


def test_mirror_is_applied_before_portrait_encoding():
  from PIL import Image
  import io
  renderer = HudRenderer(4, 2, 95)
  frame = Image.new("RGB", (4, 2), (0, 0, 0))
  frame.putpixel((0, 0), (255, 255, 255))
  renderer.set_mirror(True)
  with Image.open(io.BytesIO(renderer.encode_portrait_jpeg(frame))) as image:
    image = image.convert("RGB")
    assert image.size == (2, 4)
    assert sum(image.getpixel((x, 0))[0] for x in range(2)) > sum(image.getpixel((x, 3))[0] for x in range(2))


def test_lightweight_scene_uses_camera_free_vector_world():
  renderer = HudRenderer(1920, 462, 50)
  scene = {
    "path": [(0.0, 0.0), (20.0, 0.1), (50.0, 0.3), (100.0, 0.8)],
    "lanes": [
      {"points": [(0.0, -1.8), (50.0, -1.7), (100.0, -1.5)], "probability": 0.9},
      {"points": [(0.0, 1.8), (50.0, 1.7), (100.0, 1.5)], "probability": 0.9},
    ],
    "edges": [],
    "leads": [{"distance": 35.0, "lateral": 0.1, "relative_speed": -2.0}],
  }
  frame = renderer.render(82.0, 90.0, True, {"speed": {"road_limit_kph": 80}}, scene)
  assert frame.size == (1920, 462)
  colors = set(frame.getdata())
  assert any(blue > 180 and red < 130 for red, green, blue in colors)
  assert (255, 255, 0) not in colors


def test_carrot_3d_scene_has_vehicle_and_control_gauges_without_path_ribbon():
  renderer = HudRenderer(1920, 462, 50)
  scene = {
    "path": [(0.0, 0.0), (20.0, 0.0), (60.0, 0.0), (100.0, 0.0)],
    "lanes": renderer._fallback_lanes(),
    "edges": [],
    "leads": [{"distance": 28.0, "lateral": 0.0, "relative_speed": -1.0}],
    "accel": -0.6,
    "steer": 0.25,
  }
  frame = renderer.render(55.0, 88.0, True, {}, scene)
  colors = set(frame.getdata())
  assert (255, 78, 75) in colors
  assert (37, 211, 255) in colors
  assert any(red > 240 and green < 100 and blue < 100 for red, green, blue in colors)
  # The legacy status path color is not painted by the driving panel.
  assert HudRenderer._path_color(True, scene) not in colors


def test_cluster_overlays_and_swapped_layout_render():
  renderer = HudRenderer(1920, 462, 50)
  scene = {
    "driving_mode": 1,
    "tpms": {"fl": 30.0, "fr": 35.0, "rl": 36.0, "rr": 37.0},
    "panel_layout": 1,
    "parked": True,
    "trip_report": {"duration_s": 3600, "distance_m": 42000, "average_speed_kph": 42, "max_speed_kph": 101},
  }
  frame = renderer.render(82.0, 90.0, True, {}, scene)
  colors = set(frame.getdata())
  assert frame.size == (1920, 462)
  assert (40, 210, 125) in colors
  assert any(red > 200 and green < 80 and blue < 80 for red, green, blue in colors)

  scene["alert"] = {"text1": "TAKE CONTROL", "text2": "System Unresponsive", "status": "critical"}
  alert_frame = renderer.render(82.0, 90.0, True, {}, scene)
  colors = set(alert_frame.getdata())
  assert (225, 55, 55) in colors


def test_all_cluster_screen_modes_render_distinct_views():
  renderer = HudRenderer(1920, 462, 50)
  base_scene = {
    "system": {"cpu": 62, "temp": 57, "memory": 26, "disk": 11},
    "trip_report": {"duration_s": 3600, "distance_m": 42000,
                    "average_speed_kph": 42, "max_speed_kph": 101},
    "navi_live": True,
    "lanes": [], "edges": [], "leads": [],
  }
  navi = {"guidance_current": {"main_text": "좌회전", "distance_m": 120}}
  frames = []
  for mode in range(6):
    scene = dict(base_scene)
    scene["screen_mode"] = mode
    frames.append(renderer.render(82.0, 90.0, True, navi, scene).tobytes())
  assert len(set(frames)) == 6


def test_imperial_radar_and_extended_trip_report_render():
  renderer = HudRenderer(1920, 462, 50)
  scene = {
    "is_metric": False,
    "language": "en",
    "radar_info": 2,
    "radar_points": [{"distance": 30.0, "lateral": 1.0, "relative_speed": -2.0, "stationary": False}],
    "energy_mode": "EV",
    "screen_mode": 5,
    "trip_report": {"duration_s": 3600, "distance_m": 1609.344, "average_speed_kph": 96.56,
                    "max_speed_kph": 120.0, "engaged_time_s": 1800,
                    "max_accel": 2.1, "max_decel": -2.8},
  }
  frame = renderer.render(96.56, 112.65, True, {}, scene)
  assert frame.size == (1920, 462)
  assert (75, 220, 145) in set(frame.getdata())


def test_jpeg_quality_accepts_full_carrot_range():
  renderer = HudRenderer(1920, 462, 0)
  assert renderer.jpeg_quality == 1
  assert renderer.set_jpeg_quality(95)
  assert renderer.jpeg_quality == 95
  renderer.set_jpeg_quality(100)
  assert renderer.jpeg_quality == 95
