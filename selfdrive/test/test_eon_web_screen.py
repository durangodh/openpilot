import io
import json
from pathlib import Path
import struct
import time

import pytest
from PIL import Image

from selfdrive.eon_web_media import ScreenPreview, SCREEN_HEADER, MAX_SCREEN_BYTES


def image_bytes(kind='JPEG'):
  out = io.BytesIO()
  Image.new('RGB', (96, 54), (20, 120, 210)).save(out, kind)
  return out.getvalue()


def packet(root, now=10., kind='JPEG', captured=9.8):
  data = image_bytes(kind)
  (root / 'eon-web-screen.frame').write_bytes(SCREEN_HEADER.pack(
    b'EWS1', 1, int(captured * 1e9), int((captured + .01) * 1e9), 96, 54,
    1 if kind == 'JPEG' else 2, len(data)) + data)
  return data


def test_view_requests_qt_capture_and_serves_valid_jpeg_without_android_or_pillow_conversion(tmp_path, monkeypatch):
  # The producer already encoded the image. There is no screencap invocation.
  monkeypatch.setattr('subprocess.run', lambda *a, **k: pytest.fail('Android screencap must not run'))
  original = packet(tmp_path)
  preview = ScreenPreview(tmp_path, clock=lambda: 10.)
  assert preview.frame() == original
  assert preview.content_type == 'image/jpeg' and preview.error == ''
  assert int((tmp_path / 'eon-web-screen.request').read_text()) == 10000000000
  assert (tmp_path / 'eon-web-screen.request').stat().st_mode & 0o777 == 0o600


def test_png_fallback_is_returned_with_correct_content_type(tmp_path):
  original = packet(tmp_path, kind='PNG')
  preview = ScreenPreview(tmp_path, clock=lambda: 10.)
  assert preview.frame() == original
  assert preview.content_type == 'image/png'


def test_cached_frame_never_survives_native_ui_stopping(tmp_path):
  now = [10.]
  original = packet(tmp_path)
  preview = ScreenPreview(tmp_path, clock=lambda: now[0])
  assert preview.frame() == original
  now[0] = 10.1
  assert preview.frame() == original
  now[0] = 13.1
  assert preview.frame() is None
  assert 'UI' in preview.error


@pytest.mark.parametrize('captured', [0., 2., 11.])
def test_old_or_future_capture_is_not_served(tmp_path, captured):
  packet(tmp_path, captured=captured)
  preview = ScreenPreview(tmp_path, clock=lambda: 10.)
  assert preview.frame() is None


@pytest.mark.parametrize('corruption', ['short', 'size', 'magic', 'dimensions', 'image'])
def test_malformed_snapshot_is_not_reported_as_a_successful_capture(tmp_path, corruption):
  packet(tmp_path)
  path = tmp_path / 'eon-web-screen.frame'
  raw = bytearray(path.read_bytes())
  if corruption == 'short': raw = raw[:20]
  elif corruption == 'size': raw.pop()
  elif corruption == 'magic': raw[:4] = b'NOPE'
  elif corruption == 'dimensions': struct.pack_into('<I', raw, 24, 9999)
  elif corruption == 'image': raw[-2:] = b'no'
  path.write_bytes(raw)
  preview = ScreenPreview(tmp_path, clock=lambda: 10.)
  assert preview.frame() is None and preview.error


def test_native_error_replaces_uninformative_waiting_text(tmp_path):
  (tmp_path / 'eon-web-screen.error').write_text(json.dumps(dict(at_ns='9900000000', error='encode_failed')))
  preview = ScreenPreview(tmp_path, clock=lambda: 10.)
  assert preview.frame() is None
  assert '이미지 변환' in preview.error


def test_missing_ui_producer_reports_rebuild_requirement(tmp_path):
  preview = ScreenPreview(tmp_path, clock=lambda: 10.)
  assert preview.frame() is None
  assert 'UI 재빌드' in preview.error


def test_snapshot_symlink_is_not_followed(tmp_path):
  elsewhere = tmp_path / 'elsewhere'; elsewhere.mkdir(); packet(elsewhere)
  (tmp_path / 'eon-web-screen.frame').symlink_to(elsewhere / 'eon-web-screen.frame')
  assert ScreenPreview(tmp_path, clock=lambda: 10.).frame() is None


def test_request_write_failure_is_visible(tmp_path):
  preview = ScreenPreview(tmp_path / 'missing', clock=lambda: 10.)
  assert preview.frame() is None
  assert '요청 파일' in preview.error
