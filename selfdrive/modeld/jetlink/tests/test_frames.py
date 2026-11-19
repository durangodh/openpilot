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
  if (argc > 3) std::printf("%d %u", int(ok), unsigned(output.status()));
  else std::printf("%d", int(ok));
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


def test_native_export_reports_demand_and_file_creation_failure(exporter, tmp_path):
  demand, path = tmp_path / 'demand', tmp_path / 'frame'
  assert subprocess.check_output([str(exporter), str(demand), str(path), 'status']) == b'0 111'
  demand.touch()
  missing = tmp_path / 'missing' / 'frame'
  assert subprocess.check_output([str(exporter), str(demand), str(missing), 'status']) == b'0 113'
  assert subprocess.check_output([str(exporter), str(demand), str(path), 'status']) == b'1 116'


def test_actual_gpu_export_reads_its_own_single_frame_buffer(tmp_path):
  compiler = shutil.which('g++')
  if not compiler: pytest.skip('host C++ compiler unavailable')
  root = Path(__file__).resolve().parents[4]
  source = (root / 'selfdrive/modeld/models/commonmodel.cc').read_text()
  method = source[source.index('const float* ModelFrame::current_for_benchmark'):source.index('void softmax(')]
  fixture = tmp_path / 'gpu_export.cc'
  fixture.write_text(r'''
#include <cassert>
#include <cstddef>
#include <memory>
using cl_mem = int;
using cl_int = int;
constexpr int CL_SUCCESS = 0, CL_TRUE = 1;
int read_calls = 0, pack_calls = 0, failure = 0;
void loadyuv_queue(int*, int, int y, int u, int v, cl_mem out) {
  ++pack_calls;
  assert(y == 1 && u == 2 && v == 3 && out == 99);
}
cl_int clEnqueueReadBuffer(int, cl_mem mem, int blocking, size_t offset, size_t bytes,
                          void *destination, int, void*, void*) {
  ++read_calls;
  assert(mem == 99 && blocking == CL_TRUE && offset == 0 && bytes == 8*sizeof(float));
  auto data = static_cast<float*>(destination);
  for (int i=0; i<8; ++i) data[i] = float(20+i);
  return failure;
}
class ModelFrame {
public:
  static constexpr int MODEL_FRAME_SIZE = 8;
  std::unique_ptr<float[]> input_frames{new float[16]};
  int q=0, loadyuv=0, y_cl=1, u_cl=2, v_cl=3, net_input_cl=99;
  const float* current_for_benchmark(float*, cl_mem*);
};
''' + method + r'''
int main() {
  ModelFrame model;
  cl_mem private_backend = 123;
  auto current = model.current_for_benchmark(nullptr, &private_backend);
  assert(current && current[0]==20 && current[7]==27);
  assert(read_calls==1 && pack_calls==1 && private_backend==123);
  failure = -5;
  assert(model.current_for_benchmark(nullptr, &private_backend)==nullptr);
  float cpu[16] = {};
  assert(model.current_for_benchmark(cpu, nullptr)==cpu+8);
  assert(read_calls==2 && pack_calls==2);
  assert(model.current_for_benchmark(nullptr, nullptr)==nullptr);
}
''')
  binary = tmp_path / 'gpu_export'
  subprocess.run([compiler, '-std=c++14', '-Wall', '-Werror', str(fixture), '-o', str(binary)], check=True)
  subprocess.run([str(binary)], check=True)
