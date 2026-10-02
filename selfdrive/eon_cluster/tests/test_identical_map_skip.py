#!/usr/bin/env python3
"""Identical re-sent map/overlay images must not rewrite the files remote_hud forwards."""
import os
import struct
import sys
import tempfile
import types
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
try:
  import common.params  # noqa: F401,E402
except ImportError:
  params_module = types.ModuleType("common.params")
  params_module.Params = object
  sys.modules["common.params"] = params_module

from selfdrive import carrot_navi_server as server  # noqa: E402


def frame(body, fmt=2, seq=0):
  return server.BINARY_HEADER.pack(b"CNV2", 2, 1, fmt, 0, 0, 0, seq, 0, len(body), 640, 384) + body


def main():
  work = tempfile.mkdtemp()
  server.MAP_FILE = os.path.join(work, "map.jpg")
  server.OVERLAY_FILES["crossroad_expanded"] = os.path.join(work, "crossroad.png")
  server.map_render_fps = lambda params: 1e9         # no rate gate in this check
  state = server.NaviState()
  state._configured_source = lambda: server.SOURCE_NAVER
  writes = []
  real_rename = os.rename
  def counting_rename(src, dst):
    writes.append(dst)
    real_rename(src, dst)
  server.os.rename = counting_rename

  jpeg_a = b"\xff\xd8" + b"A" * 2000 + b"\xff\xd9"
  jpeg_b = b"\xff\xd8" + b"B" * 2000 + b"\xff\xd9"
  state.update_map(server.SOURCE_NAVER, frame(jpeg_a, seq=1))
  state.update_map(server.SOURCE_NAVER, frame(jpeg_a, seq=2))   # repeat of the same frame
  state.update_map(server.SOURCE_NAVER, frame(jpeg_b, seq=3))
  assert writes.count(server.MAP_FILE) == 2, writes
  os.unlink(server.MAP_FILE)                                   # e.g. stale clear
  state.update_map(server.SOURCE_NAVER, frame(jpeg_b, seq=4))
  assert writes.count(server.MAP_FILE) == 3, writes

  target = server.OVERLAY_FILES["crossroad_expanded"]
  state.update_overlay(server.SOURCE_NAVER, "crossroad_expanded", frame(jpeg_a))
  state.update_overlay(server.SOURCE_NAVER, "crossroad_expanded", frame(jpeg_a))
  assert writes.count(target) == 1, writes
  state.update_overlay(server.SOURCE_NAVER, "crossroad_expanded", frame(jpeg_b))
  assert writes.count(target) == 2, writes
  server.os.rename = real_rename
  print("identical map/overlay frames are not rewritten")


if __name__ == "__main__":
  main()
