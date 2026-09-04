"""Low-rate road-camera preview for the S9 display-only vehicle detector.

The EON only downsizes and JPEG-encodes a frame.  Object detection runs in the
Android HUD app, and its results never return to radar, planning, or controls.
"""

import os
import time

import numpy as np
from PIL import Image

from cereal.visionipc.visionipc_pyx import VisionIpcClient, VisionStreamType
from common.params import Params


PREVIEW_FILE = "/dev/shm/eon_hud_camera.jpg"
PREVIEW_TEMP_FILE = PREVIEW_FILE + ".tmp"
PREVIEW_SIZE = (320, 240)
JPEG_QUALITY = 58
PARAM_ENABLED = "EonClusterHudVisionDetector"
PARAM_HUD_ENABLED = "EonClusterHud"
PARAM_CONNECTED = "EonClusterHudConnected"
PARAM_FPS = "EonClusterHudVisionDetectorFps"


def _enabled(params):
  return (params.get_bool(PARAM_HUD_ENABLED) and
          params.get_bool(PARAM_ENABLED) and
          params.get_bool(PARAM_CONNECTED))


def _fps(params):
  try:
    raw = params.get(PARAM_FPS)
    value = int(raw) if raw is not None else 2
  except (TypeError, ValueError):
    value = 2
  # Two frames per second is enough for display boxes and bounds EON work.
  return max(1, min(2, value))


def _remove_preview():
  for path in (PREVIEW_TEMP_FILE, PREVIEW_FILE):
    try:
      os.unlink(path)
    except OSError:
      pass


def _write_preview(buffer, width, height, stride):
  # RGB VisionIPC on EON is BGR24 with a padded row stride.  Rebuild only the
  # visible rows, swap to RGB, then let Pillow downscale in one pass.
  # Use the same proven VisionBuf slicing path as camerad/snapshot.py.  Some
  # older EON Cython buffers implement slicing but not a direct ndarray view.
  packed = np.hstack([buffer[row * stride:row * stride + width * 3]
                      for row in range(height)])
  bgr = packed.reshape(height, width, 3)
  rgb = np.ascontiguousarray(bgr[:, :, ::-1])
  image = Image.fromarray(rgb, "RGB")
  image = image.resize(PREVIEW_SIZE, Image.BILINEAR)
  image.save(PREVIEW_TEMP_FILE, "JPEG", quality=JPEG_QUALITY, optimize=False)
  os.replace(PREVIEW_TEMP_FILE, PREVIEW_FILE)


def main():
  try:
    os.nice(10)
  except OSError:
    pass
  params = Params()
  client = None
  next_frame = 0.0
  while True:
    if not _enabled(params):
      _remove_preview()
      client = None
      next_frame = 0.0
      time.sleep(0.5)
      continue

    if client is None:
      client = VisionIpcClient("camerad", VisionStreamType.VISION_STREAM_RGB_ROAD, True)
      if not client.connect(False):
        client = None
        time.sleep(0.5)
        continue

    try:
      frame = client.recv()
      if frame is None:
        client = None
        time.sleep(0.1)
        continue
      now = time.monotonic()
      if now < next_frame:
        continue
      next_frame = now + 1.0 / _fps(params)
      _write_preview(frame, client.width, client.height, client.stride)
    except Exception as exc:
      print("S9 camera preview failed: %s" % exc, flush=True)
      _remove_preview()
      client = None
      time.sleep(1.0)


if __name__ == "__main__":
  main()
