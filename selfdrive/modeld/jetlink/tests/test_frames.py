import fcntl
import os
import shutil
import subprocess
import time
from pathlib import Path
import pytest
from selfdrive.modeld.jetlink.client import MAP_BYTES, WARPED_BYTES
from selfdrive.modeld.jetlink.frames import Frames, FRAME_HEADER, MAP_HEADER, route_map


@pytest.fixture(scope='module')
def exporter(tmp_path_factory):
  compiler = shutil.which('g++')
  if not compiler:
    pytest.skip('host C++ compiler unavailable')
  tmp_path = tmp_path_factory.mktemp('exporter')
  source = tmp_path / 'exporter.cc'
  root = Path(__file__).resolve().parents[4]
  source.write_text('''
#include "selfdrive/modeld/jetlink/frame_export.h"
#include <vector>
#include <cstdio>
int main(int argc, char **argv) {
  eon_jetlink::FrameExport output(argv[1], argv[2]);
  std::vector<float> a(eon_jetlink::VIEW_SIZE, 12), b(eon_jetlink::VIEW_SIZE, 220);
  float desire[8] = {0,1,0,0,0,0,0,0}, traffic[2] = {1,0};
  bool ok = output.publish(a.data(), b.data(), desire, traffic, 44,
    eon_jetlink::monotonic_ns(), 55, 3);
  std::printf("%d", int(ok));
  return 0;
}
''')
  binary = tmp_path / 'exporter'
  subprocess.run([compiler, '-std=c++14', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                  '-I', str(root), str(source), '-o', str(binary)], check=True)
  return binary


def test_native_export_is_opt_in_and_python_reads_cross_language_layout(exporter, tmp_path):
  demand, path = tmp_path / 'demand', tmp_path / 'frame'
  assert subprocess.check_output([str(exporter), str(demand), str(path)]) == b'0'
  assert not path.exists()
  demand.touch()
  assert subprocess.check_output([str(exporter), str(demand), str(path)]) == b'1'
  reader = Frames(path)
  try:
    frame = reader.latest()
    assert frame['frame_id'] == 44 and frame['flags'] == 3
    assert frame['desire'][1] == 1 and frame['traffic'] == [1, 0]
    assert frame['images'][:196608] == bytes([12]) * 196608
    assert frame['images'][196608:] == bytes([220]) * 196608
    assert reader.latest(44) is None
    assert reader.latest(now=time.monotonic() + 1) is None
  finally:
    reader.close()
  os.utime(demand, (time.time() - 5, time.time() - 5))
  assert subprocess.check_output([str(exporter), str(demand), str(path)]) == b'0'


def test_busy_reader_drops_optional_native_export_instead_of_waiting(exporter, tmp_path):
  demand, path = tmp_path / 'demand', tmp_path / 'frame'
  demand.touch()
  path.write_bytes(b'x' * (96 + WARPED_BYTES))
  with open(path, 'rb') as stream:
    fcntl.flock(stream, fcntl.LOCK_SH)
    before = path.read_bytes()
    assert subprocess.check_output([str(exporter), str(demand), str(path)], timeout=1) == b'0'
    assert path.read_bytes() == before


@pytest.mark.parametrize('flags,generation,age', [(1, 2, 0), (3, 1, 0), (3, 2, 1), (3, 2, -1)])
def test_uncalibrated_partial_stale_or_future_frame_is_rejected(tmp_path, flags, generation, age):
  now = time.monotonic()
  path = tmp_path / 'frame'
  header = FRAME_HEADER.pack(b'JLE1', 1, generation, int((now-age)*1e9), 8, flags,
                             *([0.] * 8), 1., 0., 0)
  path.write_bytes(header + bytes(WARPED_BYTES))
  reader = Frames(path)
  try:
    assert reader.latest(now=now) is None
  finally:
    reader.close()


def test_original_route_map_abi_expires_and_clears_to_black(tmp_path):
  path = tmp_path / 'map'
  now = time.monotonic()
  data = bytes([128]) * MAP_BYTES
  path.write_bytes(MAP_HEADER.pack(b'NMAP', 7, int(now*1e9), 1) + data)
  assert route_map(path, now + 0.1) == (data, 'route')
  assert route_map(path, now + 0.6) == (bytes(MAP_BYTES), 'blank')


def test_frame_published_during_snapshot_is_not_discarded_as_future(tmp_path, monkeypatch):
  from selfdrive.modeld.jetlink import frames as module
  path = tmp_path / 'frame'
  clock = [10.0]
  path.write_bytes(FRAME_HEADER.pack(b'JLE1',1,2,10_001_000_000,10,3,*([0.]*8),1.,0.,55) + bytes(WARPED_BYTES))
  original = module.mmap.mmap
  def concurrent_mapping(*args, **kwargs):
    clock[0] = 10.002  # producer wrote while the reader opened/mapped
    return original(*args, **kwargs)
  monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
  monkeypatch.setattr(module.mmap, 'mmap', concurrent_mapping)
  reader = Frames(path)
  try:
    assert reader.latest()['frame_id'] == 10
    assert reader.reason == '정상 영상'
    assert reader.latest(now=10.) is None
    assert '시각' in reader.reason
  finally:
    reader.close()
