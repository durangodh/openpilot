import json
import os
import time

import selfdrive.eon_cluster.renderer as renderer_module
from selfdrive.eon_cluster.renderer import HudRenderer, read_navi_state
from selfdrive.carrot_navi_server import MAP_RENDER_FPS, MAP_RENDER_HEIGHT, MAP_RENDER_WIDTH, manifest


def test_stale_navi_state_is_rejected(tmp_path):
  path = tmp_path / "state.json"
  path.write_text(json.dumps({"updated_at_ms": int(time.time() * 1000) - 40000}))
  assert read_navi_state(str(path)) == {}


def test_fresh_navi_state_is_loaded(tmp_path):
  path = tmp_path / "state.json"
  path.write_text(json.dumps({"updated_at_ms": int(time.time() * 1000), "route": {"remain_distance_m": 1000}}))
  assert read_navi_state(str(path))["route"]["remain_distance_m"] == 1000


def test_route_activity_rejects_stale_or_ended_destination():
  now_ms = int(time.time() * 1000)
  active = {
    "route": {"remain_distance_m": 1200},
    "stream_updated_at_ms": {"route": now_ms},
  }
  assert renderer_module._navi_route_active(active, now_ms)
  assert not renderer_module._navi_route_active(
    dict(active, stream_updated_at_ms={"route": now_ms - 4000}), now_ms)
  assert not renderer_module._navi_route_active(
    dict(active, navigation_status={"active": False}), now_ms)
  assert not renderer_module._navi_route_active({"route": {"remain_distance_m": 0}}, now_ms)


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


def test_blindspot_flags_draw_rear_quarter_vehicles():
  renderer = HudRenderer(1920, 462, 50)
  scene = {"lanes": [], "edges": [], "leads": []}
  base = renderer.render(55.0, 88.0, True, {}, scene)
  left = renderer.render(55.0, 88.0, True, {}, dict(scene, left_blindspot=True))
  right = renderer.render(55.0, 88.0, True, {}, dict(scene, right_blindspot=True))

  assert left.tobytes() != base.tobytes()
  assert right.tobytes() != base.tobytes()
  assert left.tobytes() != right.tobytes()
  assert (255, 169, 45) in set(left.getdata())
  assert (255, 169, 45) in set(right.getdata())


def test_tmap_guidance_layout_stays_fixed_when_json_briefly_drops(tmp_path, monkeypatch):
  from PIL import Image
  map_path = tmp_path / "map.jpg"
  current_path = tmp_path / "current.png"
  next_path = tmp_path / "next.png"
  lane_path = tmp_path / "lane.png"
  map_color = (41, 112, 173)
  current_color = (12, 220, 80, 255)
  next_color = (20, 140, 60, 255)
  lane_color = (240, 130, 20, 255)
  Image.new("RGB", (640, 384), map_color).save(str(map_path), quality=95)
  Image.new("RGBA", (400, 160), current_color).save(str(current_path))
  Image.new("RGBA", (240, 60), next_color).save(str(next_path))
  Image.new("RGBA", (320, 80), lane_color).save(str(lane_path))
  monkeypatch.setattr(renderer_module, "NAVI_MAP", str(map_path))
  monkeypatch.setattr(renderer_module, "NAVI_TBT_CURRENT", str(current_path))
  monkeypatch.setattr(renderer_module, "NAVI_TBT_NEXT", str(next_path))
  monkeypatch.setattr(renderer_module, "NAVI_LANE", str(lane_path))

  renderer = HudRenderer(1920, 462, 50)
  scene = {
    "lanes": [], "edges": [], "leads": [],
    "trip_report": {"duration_s": 5, "distance_m": 0},
  }
  clock = [100.0]
  monkeypatch.setattr(renderer_module.time, "monotonic", lambda: clock[0])
  navi = {
    "guidance_current": {"main_text": "TURN", "distance_m": 120},
    "route": {"remain_distance_m": 1200, "remain_time_sec": 180},
  }
  live_frame = renderer.render(55.0, 88.0, True, navi, scene)
  clock[0] = 101.0
  dropped_json_frame = renderer.render(55.0, 88.0, True, {}, scene)

  # A transient JSON read failure must not switch to the trip report or blink
  # any of the last complete native TMap overlays.
  assert live_frame.tobytes() == dropped_json_frame.tobytes()

  # Once the grace period expires without an active destination, auto mode
  # leaves the stale map and shows the trip report.
  clock[0] = 103.1
  ended_route_frame = renderer.render(55.0, 88.0, True, {}, scene)
  assert ended_route_frame.tobytes() != live_frame.tobytes()

  # Native current/next guidance is stacked at the map's upper-left.
  assert live_frame.getpixel((1375, 94)) == current_color[:3]
  assert live_frame.getpixel((1298, 216)) == next_color[:3]
  # Native lane guidance is centered along the map's bottom edge.
  assert live_frame.getpixel((1537, 410)) == lane_color[:3]


def test_tmap_stream_is_wide_and_does_not_increase_pixel_load():
  streams = manifest()["streams"]
  enabled_images = {stream["name"] for stream in streams
                    if stream["kind"] == "image" and stream["enabled"]}
  assert enabled_images == {"tbt_current_full", "tbt_next", "lane_bottom"}
  render_stream = next(stream for stream in streams if stream["name"] == "map_main")
  params = render_stream["params"]
  assert (params["width"], params["height"], params["fps"]) == (
    MAP_RENDER_WIDTH, MAP_RENDER_HEIGHT, MAP_RENDER_FPS)
  assert MAP_RENDER_WIDTH * MAP_RENDER_HEIGHT <= 480 * 540
  assert abs(float(MAP_RENDER_WIDTH) / MAP_RENDER_HEIGHT - 768.0 / 462.0) < 0.01


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
  navi = {
    "guidance_current": {"main_text": "좌회전", "distance_m": 120},
    "route": {"remain_distance_m": 1200, "remain_time_sec": 180},
  }
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


def test_scaled_image_cache_stays_bounded(tmp_path):
  from PIL import Image

  renderer_module._IMAGE_CACHE.clear()
  renderer_module._FIT_CACHE.clear()
  path = str(tmp_path / "map.jpg")
  for index in range(12):
    Image.new("RGB", (640, 384), (index * 7 % 255, 40, 60)).save(path, format="JPEG")
    # Force a distinct signature the way a live TMap stream does.
    os.utime(path, (1000 + index, 1000 + index))
    fitted = renderer_module._safe_full_image(path, (768, 462))
    assert fitted is not None and fitted.size == (768, 462)

  assert len(renderer_module._FIT_CACHE) == 1
  os.unlink(path)
  assert renderer_module._safe_full_image(path, (768, 462)) is None
  assert len(renderer_module._FIT_CACHE) == 0
