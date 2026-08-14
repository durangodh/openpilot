# EON TURZX HUD

Low-load external HUD for the EON `g_c2hud` branch. It renders a camera-free
driving scene from `modelV2` and `radarState`, reuses the existing TMap receiver
outputs in `/dev/shm`, and sends the combined JPEG dashboard to a supported
TURZX USB display.

USB transport geometries:

- `1cbe:0092` (9.2 inch, 1920x462)
- `1cbe:0123` (12.3 inch, 1920x720)

The managed EON process intentionally pins automatic connection to
`1cbe:0092`. The 12.3-inch geometry remains available to direct transport
callers but is not selected by the settings toggle.

The process is disabled by default. Enable and tune it with Params:

```sh
cd /data/openpilot
python - <<'PY'
from common.params import Params
p = Params()
p.put_bool("EonClusterHud", True)
p.put("EonClusterHudFps", "10")
p.put("EonClusterHudBrightness", "65")
p.put("EonClusterHudJpegQuality", "58")
p.put("EonClusterHudScreenMode", "1")
p.put("EonClusterHudOrientation", "0")
p.put("EonClusterHudMirror", "0")
p.put("EonClusterHudRadarInfo", "4")
p.put("EonClusterHudRadarDisplay", "0")
PY
```

Brightness `0` follows the device screen brightness. Orientation accepts `0`
or `2` (180 degrees), mirror accepts `0` or `1`, and language accepts `0`
(Korean) or `1` (English). `IsMetric` controls km/h versus mph. Radar info
modes are `0` off, `1/2` moving-vehicle speed or speed+distance, and `3/4`
all-object speed or speed+distance. Radar display mode `1` adds up to 16
read-only `liveTracks` points; it never publishes CAN or control messages.
While the panel is connected, the EON screen keeps essential speed, limit,
status, and alert overlays but skips its duplicate camera, model, lead, plot,
and TMap draws. Normal on-device rendering resumes within 500 ms after the
external HUD disconnects.

The display uses a fixed 4:2:4 layout. The left 40% is a lightweight synthetic driving scene with a
Tesla-style two-row header: current speed, gear, unit, driving mode, a live
steering-wheel indicator, cruise set speed, and active speed-camera limit.
The planned path is shown as two blue boundaries beside the ego vehicle, and
BSD detections appear as white vehicles at its rear quarters. The middle 20%
always shows system load and temperature, while the right 40% keeps the TMap
map, turn guidance, lane image, and remaining distance. No road-camera pixels
are copied or encoded.

The lightweight HUD also mirrors active openpilot alerts as outlined text
without covering the driving or navigation background. It replaces the
navigation panel with an expanded trip summary including engaged ratio and
peak acceleration/deceleration when navigation is inactive.

The cluster planned path is always blue and remains split into left and right
boundaries so the road and vehicle stay visible between them.

Right-panel modes are `1` auto navigation/report, `2` live debug, and `3`
fixed trip report.
FPS, brightness, JPEG quality, screen mode, and theme changes are
applied while the USB display remains connected. JPEG quality accepts 1-95.

HUD text normally uses Android or system TrueType fonts. Minimal EON Pillow
builds without `_imagingft` use bundled carrot-wip-derived Pretendard BMFont
atlases instead, so Latin labels, numeric values, and complete Korean Hangul
navigation text retain their requested size without FreeType. Other unsupported
glyphs are shown as `?` rather than aborting the frame. See
`selfdrive/assets/fonts/Pretendard-LICENSE.txt` for the font license.

Start at 10 FPS. The accepted FPS range is deliberately limited to 5-15 FPS
to protect EON thermal and scheduling headroom. Camera rendering, OpenGL scene
capture, and software H.264 are intentionally excluded. The display is dimmed
when the process is disabled or stopped.

`EonClusterHudConnected` reports the live USB connection state. The process
waits without rendering while the display is absent, wakes immediately for a
Linux USB hotplug event, and retains a 5-second scan fallback.
