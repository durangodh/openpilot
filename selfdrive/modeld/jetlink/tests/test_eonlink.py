"""Exercise the exact Java relay packaged in the APK, with a mock engine.

These tests validate protocol fidelity, not Android/NPU/real-world latency.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import time
from types import SimpleNamespace as NS

import numpy as np
import pytest

from selfdrive.modeld.jetlink.eonlink import EonClient, ENVELOPE, LIMIT
from selfdrive.modeld.jetlink.client import LinkError, WARPED_BYTES, MAP_BYTES
from selfdrive.modeld.jetlink.tests.test_protocol import Phone
from selfdrive.modeld.jetlink.tests.test_benchmark import paths
from selfdrive.modeld.jetlink import benchmark as B
from selfdrive import eon_web_jetlink as W

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope='module')
def java_relay(tmp_path_factory):
  home = os.environ.get('JAVA_HOME', '')
  javac = str(Path(home) / 'bin/javac') if home else shutil.which('javac')
  java = str(Path(home) / 'bin/java') if home else shutil.which('java')
  if not javac or not java:
    pytest.skip('JDK needed to test the APK relay')
  classes = tmp_path_factory.mktemp('eonlink-java')
  app = ROOT / 'tools/eon_link_android'
  sources = [app / 'src/ai/comma/eonlink' / (name + '.java') for name in ('Wire', 'Relay')]
  subprocess.run([javac, '-d', str(classes)] + [str(p) for p in sources] + [str(app / 'tests/RelayHarness.java')], check=True)
  return java, classes


class Relay:
  def __init__(self, java_relay, backend):
    java, classes = java_relay
    self.process = subprocess.Popen([java, '-cp', str(classes), 'RelayHarness', str(backend)],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    self.port = int(self.process.stdout.readline())
  def client(self, compression=False, check=lambda: None):
    return EonClient(socket.create_connection(('127.0.0.1', self.port), timeout=2), check, compression)
  def close(self):
    self.process.communicate(b'\n', timeout=5)
    assert self.process.returncode == 0


@pytest.mark.parametrize('compression', [False, True])
def test_java_phone_relay_preserves_n16_images_maps_and_echo_without_engine(compression, java_relay):
  phone = Phone()
  relay = Relay(java_relay, phone.port)
  client = relay.client(compression)
  try:
    for data in (b'x' * 32, b'\x07' * WARPED_BYTES, np.random.RandomState(1).bytes(WARPED_BYTES)):
      timing = client.probe(data)
      assert timing['roundtrip_ms'] > 0 and timing['phone_receive_ms'] >= 0
      assert phone.inputs == []  # Echo never runs the model.
    assert client.prepare()['backend'] == 'mock'
    images = np.random.RandomState(42).bytes(WARPED_BYTES)
    packed = np.arange(59, dtype=np.float32)
    route = b'\x93' * MAP_BYTES
    for frame, nav_map, reset in ((1, route, True), (2, route, False), (3, bytes(MAP_BYTES), False)):
      values, timing = client.infer(frame, images, packed, nav_map, reset)
      assert len(values) == 18580
      assert phone.inputs[-1][2][8:8 + WARPED_BYTES] == images
      assert phone.maps[-1] == nav_map
      assert timing['phone_engine_ms'] > 0 and timing['phone_receive_ms'] >= 0
      assert timing['plain_tx_bytes'] >= timing['tx_bytes']
    assert not (phone.inputs[1][1] & 8)  # Same map retained by actual Java path.
  finally:
    client.close()
    relay.close()
    phone.join()


def test_java_relay_rejects_oversized_and_corrupt_compressed_frames(java_relay):
  # No backend exists: rejection must happen before opening a model socket.
  relay = Relay(java_relay, 1)
  try:
    cases = [(0, LIMIT + 1, 1, b'x'), (1, 64, 4, b'nope'), (0, 64, 1, b'x')]
    for flags, plain, size, data in cases:
      with socket.create_connection(('127.0.0.1', relay.port), timeout=2) as sock:
        sock.sendall(ENVELOPE.pack(b'EON1', 1, flags, 0, 1, plain, size, 0, 0, 0) + data)
        try:
          assert sock.recv(1) == b''
        except ConnectionResetError:
          pass
      time.sleep(.02)
  finally:
    relay.close()


def test_eon_benchmark_reports_echo_and_phone_timings_without_control(monkeypatch, tmp_path, java_relay):
  paths(monkeypatch, tmp_path)
  phone = Phone()
  relay = Relay(java_relay, phone.port)
  args = NS(host='127.0.0.1', port=relay.port, seconds=.2, source='synthetic', standalone=True,
            transport='eonlink', compression=False, report=tmp_path / 'report.json')
  try:
    assert B.run(args) == 0
    report = json.loads(args.report.read_text())
    assert report['transport'] == 'eonlink-v1' and report['timing_version'] == 3
    assert report['link_probe']['image_mean_ms'] > 0
    assert report['driving_ready'] is False and report['frames'] > 0
    assert report['last']['phone_engine_ms'] > 0
  finally:
    relay.close()
    phone.join()


def test_web_accepts_auto_only_for_eonlink_and_persists_transport_arguments(monkeypatch, tmp_path):
  monkeypatch.setattr(W, 'ROOT', tmp_path)
  calls = []
  monkeypatch.setattr(W.subprocess, 'Popen', lambda args, **kwargs: calls.append(args) or NS(pid=99, poll=lambda: None))
  payload = dict(host='auto', port=5600, seconds=60, source='synthetic', transport='eonlink', compression=True)
  controller = W.JetlinkBench('/repo')
  assert controller.start(dict(payload, transport='jetlink'))[0] == 400
  assert controller.start(dict(payload, compression='true'))[0] == 400
  assert controller.start(payload)[0] == 202
  assert calls[0][-3:] == ['--transport', 'eonlink', '--compression']


def test_eon_timing_aggregation_keeps_phone_engine_separate():
  stats = B.Metrics()
  timing = dict(roundtrip_ms=100, server_ms=37, gpu_ms=36, phone_receive_ms=50,
                phone_inflate_ms=2, phone_engine_ms=39, plain_tx_bytes=400000, compress_ms=3)
  for _ in range(21):
    stats.add(timing, 110)
  value = stats.snapshot()
  assert value['phone_receive_mean_ms'] == 50
  assert value['phone_engine_mean_ms'] == 39
  assert value['compress_mean_ms'] == 3
  assert value['driving_ready'] is False
