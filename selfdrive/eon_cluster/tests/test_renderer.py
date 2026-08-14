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


def test_path_is_two_blue_boundaries_beside_the_ego_car():
  from PIL import Image, ImageDraw
  renderer = HudRenderer(1920, 462, 50)
  panel = (0, 0, 1150, 462)
  image = Image.new("RGB", (1150, 462), (0, 0, 0))
  renderer._draw_path(image, ImageDraw.Draw(image), panel,
                      [(0.0, 0.0), (10.0, 0.0), (30.0, 0.0), (80.0, 0.0)],
                      True, {"show_path_status_color": False})
  blue = (24, 126, 224)
  assert blue in set(image.getdata())
  assert sum(image.getpixel((x, 461)) == blue for x in range(450, 700)) < 10
  _, near_y = renderer._project(panel, 10.0, 0.0)
  blue_x = [x for x in range(400, 750) if image.getpixel((x, near_y)) == blue]
  runs = sum(index == 0 or blue_x[index] > blue_x[index - 1] + 1 for index in range(len(blue_x)))
  assert runs == 2
  assert image.getpixel((575, near_y)) != blue


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
  # The production-cluster style planned route stays a fixed narrow blue.
  assert (24, 126, 224) in colors


def test_requested_header_has_wheel_mode_cruise_camera_and_split_path():
  renderer = HudRenderer(1920, 462, 50)
  scene = {
    "path": [(0.0, 0.0), (20.0, 0.0), (60.0, 0.0), (100.0, 0.0)],
    "lanes": renderer._fallback_lanes(),
    "edges": [],
    "leads": [{"distance": 28.0, "lateral": 0.0, "relative_speed": -1.0}],
    "accel": -0.6,
    "steer": 0.25,
    "steering_angle_deg": 32.0,
    "gear": "D",
    "driving_mode": 2,
    "camera_limit_speed": 80,
  }
  frame = renderer.render(55.0, 88.0, True, {}, scene)
  colors = set(frame.getdata())
  assert (18, 95, 225) in colors
  assert (20, 160, 92) in colors
  assert (220, 45, 45) in colors
  assert (24, 126, 224) in colors


def test_curve_geometry_is_temporally_stabilized_for_external_hud():
  renderer = HudRenderer(1920, 462, 60)
  straight = {
    "path": [(0.0, 0.0), (40.0, 0.0), (100.0, 0.0)],
    "lanes": [{"index": 1, "probability": 0.9,
               "points": [(0.0, -1.8), (40.0, -1.8), (100.0, -1.8)]}],
    "edges": [{"index": 0, "probability": 0.8,
               "points": [(0.0, -4.0), (40.0, -4.0), (100.0, -4.0)]}],
  }
  curve = {
    "path": [(0.0, 0.0), (40.0, 3.0), (100.0, 9.0)],
    "lanes": [{"index": 1, "probability": 0.9,
               "points": [(0.0, -1.8), (40.0, 1.2), (100.0, 7.2)]}],
    "edges": [{"index": 0, "probability": 0.8,
               "points": [(0.0, -4.0), (40.0, -1.0), (100.0, 5.0)]}],
  }
  renderer._stabilize_scene_geometry(straight)
  first_curve = renderer._stabilize_scene_geometry(curve)
  lane_far = first_curve["lanes"][0]["points"][-1][1]
  assert first_curve["edges"] == []
  path_far = first_curve["path"][-1][1]
  assert -1.8 < lane_far < 0.0
  assert 0.0 < path_far < 1.0

  # Repeated frames converge toward the real bend instead of freezing it.
  later_curve = first_curve
  for _ in range(12):
    later_curve = renderer._stabilize_scene_geometry(curve)
  assert later_curve["lanes"][0]["points"][-1][1] > lane_far
  assert later_curve["path"][-1][1] > path_far


def test_vehicle_sprites_are_cached_realistic_and_shadow_free():
  from PIL import Image, ImageDraw
  renderer = HudRenderer(1920, 462, 60)

  base_sprite = renderer._build_vehicle_base_sprite("ego")
  lead_base = renderer._build_vehicle_base_sprite("lead")
  braking_base = renderer._build_vehicle_base_sprite("lead", braking=True)
  assert base_sprite.size == (240, 190)
  for sprite in (base_sprite, lead_base, braking_base):
    assert not any(red > green + 50 and red > blue + 50 and alpha > 0
                   for red, green, blue, alpha in sprite.getdata())
  assert (58, 64, 69, 255) in set(base_sprite.getdata())
  assert (152, 157, 161, 255) in set(lead_base.getdata())
  assert sum((58, 64, 69)) < sum((152, 157, 161))
  ego_sprite = renderer._vehicle_sprite("ego", 104, 96)
  assert ego_sprite is renderer._vehicle_sprite("ego", 105, 97)
  traffic_sprite = renderer._vehicle_sprite("traffic", 104, 96, marker=True)
  assert ego_sprite.mode == "RGBA"
  assert ego_sprite.width > ego_sprite.height
  assert ego_sprite.tobytes() != traffic_sprite.tobytes()
  assert len(renderer._vehicle_sprite_cache) == 2

  vehicle = Image.new("RGB", (260, 200), (0, 0, 0))
  renderer._draw_vehicle_shape(vehicle, 130, 190, 104, 96, "ego")
  vehicle_colors = set(vehicle.getdata())
  assert vehicle.getbbox() is not None
  assert (1, 3, 6) not in vehicle_colors
  assert (137, 168, 187) not in vehicle_colors

  block = Image.new("RGB", (180, 120), (0, 0, 0))
  renderer._draw_world_block(ImageDraw.Draw(block), 90, 100, 50, 32, (54, 207, 121))
  block_colors = set(block.getdata())
  assert (2, 5, 7) not in block_colors
  assert (109, 255, 176) not in block_colors


def test_vehicle_sprite_cache_stays_bounded():
  renderer = HudRenderer(1920, 462, 60)
  for width in range(20, 300, 4):
    renderer._vehicle_sprite("traffic", width, width, marker=True)
  assert len(renderer._vehicle_sprite_cache) == 48


def test_tesla_style_vehicle_scale_keeps_ego_prominent_and_leads_smooth():
  renderer = HudRenderer(1920, 462, 60)
  panel = (0, 0, 1150, 462)
  panel_w = panel[2] - panel[0]
  panel_h = panel[3] - panel[1]
  ego_w = max(112, int(panel_w * 0.115))
  ego_h = max(104, int(panel_h * 0.265))

  from PIL import Image, ImageDraw
  frame = Image.new("RGB", (1920, 462), (239, 241, 242))
  draw = ImageDraw.Draw(frame)
  renderer._draw_lead(frame, draw, panel,
                      {"distance": 35.0, "lateral": 0.0, "relative_speed": 0.0},
                      True, 0, True)
  first_w, first_h, _ = renderer._lead_size_history["primary"]
  assert first_w < ego_w * 0.65
  assert first_h < ego_h * 0.65

  renderer._draw_lead(frame, draw, panel,
                      {"distance": 34.0, "lateral": 0.0, "relative_speed": 0.0},
                      True, 0, True)
  second_w, second_h, _ = renderer._lead_size_history["primary"]
  # A one-metre model change must move gradually instead of snapping to a
  # different cached sprite bucket.
  assert 0.0 < second_w - first_w < 1.0
  assert 0.0 < second_h - first_h < 1.0


def test_stationary_radar_uses_green_3d_world_block():
  renderer = HudRenderer(1920, 462, 60)
  scene = {
    "lanes": [], "edges": [], "leads": [],
    "radar_info": 4,
    "radar_points": [{"distance": 24.0, "lateral": 2.0,
                      "relative_speed": 0.0, "stationary": True}],
  }
  colors = set(renderer.render(45.0, 70.0, True, {}, scene).getdata())
  assert (54, 207, 121) in colors


def test_blindspot_flags_draw_rear_quarter_vehicles():
  renderer = HudRenderer(1920, 462, 50)
  scene = {"lanes": [], "edges": [], "leads": []}
  base = renderer.render(55.0, 88.0, True, {}, scene)
  left = renderer.render(55.0, 88.0, True, {}, dict(scene, left_blindspot=True))
  right = renderer.render(55.0, 88.0, True, {}, dict(scene, right_blindspot=True))

  assert left.tobytes() != base.tobytes()
  assert right.tobytes() != base.tobytes()
  assert left.tobytes() != right.tobytes()
  blindspot_sprite = renderer._build_vehicle_base_sprite("blindspot", marker=True)
  assert (235, 238, 240, 220) in set(blindspot_sprite.getdata())


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
    "camera_limit_speed": 80,
    "gear": "D",
    "steering_angle_deg": -18.0,
    "panel_layout": 1,
    "parked": True,
    "trip_report": {"duration_s": 3600, "distance_m": 42000, "average_speed_kph": 42, "max_speed_kph": 101},
  }
  frame = renderer.render(82.0, 90.0, True, {}, scene)
  colors = set(frame.getdata())
  assert frame.size == (1920, 462)
  assert (226, 144, 38) in colors
  assert (220, 45, 45) in colors

  scene["alert"] = {"text1": "TAKE CONTROL", "text2": "System Unresponsive", "status": "critical"}
  alert_frame = renderer.render(82.0, 90.0, True, {}, scene)
  colors = set(alert_frame.getdata())
  assert (255, 82, 96) in colors


def test_cluster_alert_is_background_free_and_stays_in_driving_panel():
  from PIL import Image, ImageDraw, ImageChops
  renderer = HudRenderer(1920, 462, 50)
  background = (31, 67, 101)
  base = Image.new("RGB", (1920, 462), background)
  frame = base.copy()
  driving_box = (0, 0, 1149, 462)
  renderer._draw_alert(ImageDraw.Draw(frame), {
    "text1": "TAKE CONTROL",
    "text2": "System Unresponsive",
    "status": "critical",
    "size": "full",
  }, driving_box)

  changed = ImageChops.difference(base, frame).getbbox()
  assert changed is not None
  assert changed[0] >= driving_box[0]
  assert changed[2] <= driving_box[2]
  assert frame.getpixel((20, 20)) == background
  assert frame.getpixel((1500, 231)) == background
  assert (255, 82, 96) in set(frame.getdata())


def test_cluster_alert_promotes_detail_when_title_is_empty():
  from PIL import Image, ImageDraw, ImageChops
  renderer = HudRenderer(1920, 462, 50)
  base = Image.new("RGB", (1920, 462), (7, 12, 18))
  frame = base.copy()
  renderer._draw_alert(ImageDraw.Draw(frame), {
    "text1": "",
    "text2": "System Unresponsive",
    "status": "warning",
    "size": "mid",
  }, (0, 0, 1149, 462))
  assert ImageChops.difference(base, frame).getbbox() is not None
  assert (255, 174, 82) in set(frame.getdata())


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
    "camera_limit_speed": 80,
    "screen_mode": 5,
    "trip_report": {"duration_s": 3600, "distance_m": 1609.344, "average_speed_kph": 96.56,
                    "max_speed_kph": 120.0, "engaged_time_s": 1800,
                    "max_accel": 2.1, "max_decel": -2.8},
  }
  frame = renderer.render(96.56, 112.65, True, {}, scene)
  assert frame.size == (1920, 462)
  without_radar = renderer.render(96.56, 112.65, True, {}, dict(scene, radar_points=[]))
  assert frame.tobytes() != without_radar.tobytes()


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


def test_requested_clean_header_omits_footer_status_dots():
  renderer = HudRenderer(1920, 462, 60)
  scene = {
    "lanes": [], "edges": [], "leads": [],
    "footer": {"ip": "10.73.140.85", "fps": 11.4},
  }
  frame = renderer.render(34.0, 88.0, True, {}, scene)
  colors = set(frame.getdata())
  assert (39, 219, 139) not in colors
  assert (232, 168, 62) not in colors

  slow = dict(scene, footer={"ip": "10.73.140.85", "fps": 2.0})
  assert (232, 168, 62) not in set(renderer.render(34.0, 88.0, True, {}, slow).getdata())


def test_lane_markings_are_split_into_world_distance_dashes():
  fragments = HudRenderer._lane_dash_fragments([(0.0, 0.0), (30.0, 3.0)])
  ranges = [(round(start[0], 1), round(end[0], 1)) for start, end in fragments]
  assert ranges == [(0.0, 4.0), (9.0, 13.0), (18.0, 22.0), (27.0, 30.0)]
  # Lateral interpolation keeps each dash on the detected curve.
  assert abs(fragments[1][0][1] - 0.9) < 1e-9
  assert abs(fragments[1][1][1] - 1.3) < 1e-9


def test_only_detected_lanes_use_pale_gray_on_light_road():
  from PIL import Image, ImageDraw
  renderer = HudRenderer(1920, 462, 60)

  def lane(offset, index, probability=0.95):
    return {"points": [(d, offset) for d in (0.0, 20.0, 60.0, 120.0)],
            "probability": probability, "index": index}

  scene = {
    "lanes": [lane(-5.5, 0, 0.7), lane(-1.85, 1), lane(1.85, 2), lane(5.5, 3, 0.7)],
    "edges": [{"points": [(0.0, -7.0), (120.0, -7.0)], "probability": 1.0}],
    "leads": [],
  }
  colors = set(renderer.render(60.0, 60.0, True, {}, scene).getdata())
  assert (150, 152, 153) in colors
  assert not any(red > 230 and 170 < green < 225 and blue < 90
                 for red, green, blue in colors)

  background = Image.new("RGB", (1150, 462), (0, 0, 0))
  renderer._draw_road_surface(background, (0, 0, 1150, 462))
  assert min(background.getpixel((10, 10))) > 225
  assert min(background.getpixel((575, 450))) > 200

  # Empty model output draws no invented fallback and roadEdges make no draw call.
  renderer._fallback_lanes = lambda: (_ for _ in ()).throw(AssertionError("fallback used"))
  edge_calls = []
  renderer._draw_polyline = lambda *_args, **_kwargs: edge_calls.append(True)
  frame = Image.new("RGB", (1920, 462), (0, 0, 0))
  renderer._draw_driving_panel(frame, ImageDraw.Draw(frame), (0, 0, 1150, 462),
                               0.0, 0.0, False,
                               {"lanes": [], "edges": scene["edges"], "leads": []})
  assert edge_calls == []


def test_detected_lane_is_held_for_three_frames_only():
  renderer = HudRenderer(1920, 462, 60)
  lane = {"index": 1, "probability": 0.9,
          "points": [(0.0, -1.8), (40.0, -1.7), (100.0, -1.4)]}
  scene = {"lanes": [lane], "edges": [], "path": []}
  assert len(renderer._stabilize_scene_geometry(scene)["lanes"]) == 1
  empty = {"lanes": [], "edges": [], "path": []}
  for _ in range(3):
    held = renderer._stabilize_scene_geometry(empty)
    assert len(held["lanes"]) == 1
    assert held["edges"] == []
  assert renderer._stabilize_scene_geometry(empty)["lanes"] == []


def test_live_guidance_text_survives_a_json_drop():
  renderer = HudRenderer(1920, 462, 60)
  navi = {
    "guidance_current": {"main_text": "광주경찰청", "distance_m": 254},
    "guidance_next": {"distance_m": 513},
    "route": {"remain_distance_m": 4700, "remain_time_sec": 540},
  }
  assert renderer._guidance_line(navi["guidance_current"], True, "ko").startswith("254")
  assert "광주경찰청" in renderer._guidance_line(navi["guidance_current"], True, "ko")
  # road_name is the fallback when the stream carries no main_text.
  assert renderer._guidance_line({"road_name": "외곽순환"}, True, "ko") == "외곽순환"
  assert renderer._guidance_line({}, True, "ko") == ""
  assert renderer._guidance_line(None, True, "ko") == ""

  from PIL import ImageDraw, Image
  frame = Image.new("RGB", (1920, 462))
  draw = ImageDraw.Draw(frame)
  box = (1152, 0, 1920, 462)
  renderer._draw_navi_text(draw, box, navi, "ko", True)
  assert renderer._navi_text_cache["current"].startswith("254")
  # An empty read must reuse the cached strings rather than blank the panel.
  renderer._draw_navi_text(draw, box, {}, "ko", True)
  assert renderer._navi_text_cache["current"].startswith("254")


def test_gear_and_turn_signals_are_optional():
  renderer = HudRenderer(1920, 462, 60)
  bare = {"lanes": [], "edges": [], "leads": []}
  assert renderer.render(30.0, 0.0, False, {}, bare).size == (1920, 462)
  wired = dict(bare, gear=3, blinkers={"left": True, "right": True})
  assert renderer.render(30.0, 0.0, False, {}, wired).size == (1920, 462)
  # No blinker data must not raise or draw.
  assert renderer.render(30.0, 0.0, False, {}, dict(bare, blinkers=None)).size == (1920, 462)
