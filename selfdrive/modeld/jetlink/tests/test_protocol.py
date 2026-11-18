import json
import socket
import struct
import threading
import time
import numpy as np
import pytest
from selfdrive.modeld.jetlink.client import (
  Client, HEADER, MAGIC, VERSION, PADDED, SPEC, WARPED_BYTES, MAP_BYTES, LinkError, navigation_input)


def receive(sock):
  def exact(size):
    data = b''
    while len(data) < size:
      part = sock.recv(size - len(data))
      if not part:
        raise EOFError()
      data += part
    return data
  magic, version, kind, sequence, flags, size, _ = HEADER.unpack(exact(32))
  assert (magic, version) == (MAGIC, VERSION)
  payload = exact(size)
  if flags & PADDED:
    exact(1)
  return kind, sequence, payload


def send(sock, kind, sequence, payload, fragment=False):
  padded = (32 + len(payload)) % 512 == 0
  data = HEADER.pack(MAGIC, VERSION, kind, sequence, PADDED if padded else 0, len(payload), 0) + payload
  data += b'\0' if padded else b''
  if fragment:
    for i in range(0, len(data), 31):
      sock.sendall(data[i:i+31])
  else:
    sock.sendall(data)


class Phone:
  """Mock protocol peer: zero output is never presented as N16 inference."""
  def __init__(self, loaded=True, bad_spec=False, reply='ok'):
    self.listener = socket.socket()
    self.listener.bind(('127.0.0.1', 0))
    self.listener.listen(1)
    self.port = self.listener.getsockname()[1]
    self.loaded, self.bad_spec, self.reply = loaded, bad_spec, reply
    self.inputs, self.errors = [], []
    self.thread = threading.Thread(target=self.serve, daemon=True)
    self.thread.start()

  def serve(self):
    connection = None
    try:
      connection, _ = self.listener.accept()
      connection.settimeout(2)
      while True:
        kind, sequence, payload = receive(connection)
        if kind == 1:
          response = {'protocol': 3, 'loaded': SPEC['sha256'] if self.loaded else None,
                      'device': 'mock protocol peer', 'backend': 'mock'}
          send(connection, 2, sequence, json.dumps(response).encode(), True)
        elif kind == 3:
          assert json.loads(payload)['sha256'] == SPEC['sha256']
          spec = dict(SPEC, nbytes=1) if self.bad_spec else SPEC
          send(connection, 4, sequence, json.dumps({'state': 'ready', 'spec': spec}).encode(), True)
        elif kind == 8:
          frame_id, flags = struct.unpack_from('<II', payload)
          assert len(payload) == 8 + WARPED_BYTES + 59 * 4 + (MAP_BYTES if flags & 8 else 0)
          self.inputs.append((frame_id, flags, payload))
          if self.reply == 'timeout':
            time.sleep(0.15)
            continue
          values = np.zeros(SPEC['output_shapes']['outputs'][1] - 16384, dtype='<f4')
          if self.reply == 'nan':
            values[5] = np.nan
          data = struct.pack('<IIIII', frame_id + (self.reply == 'wrong_frame'),
                             4 if self.reply == 'failed' else 0, 12000, 200, 13000) + values.tobytes()
          if self.reply == 'short':
            data = data[:-4]
          send(connection, 9, sequence, data, True)
    except (EOFError, BrokenPipeError, ConnectionResetError, socket.timeout):
      pass
    except Exception as error:
      self.errors.append(error)
    finally:
      if connection:
        connection.close()
      self.listener.close()

  def client(self):
    return Client(socket.create_connection(('127.0.0.1', self.port), timeout=1))

  def join(self):
    self.thread.join(2)
    assert not self.thread.is_alive()
    assert self.errors == []


def test_wire_shapes_fragmentation_engine_identity_and_hidden_omission():
  phone = Phone()
  client = phone.client()
  try:
    assert client.prepare()['device'] == 'mock protocol peer'
    output, timing = client.infer(34, bytes(WARPED_BYTES), np.zeros(59), bytes(MAP_BYTES), reset=True)
    assert output.shape == (18580,) and np.isfinite(output).all()
    assert timing['gpu_ms'] == 12.0
    assert phone.inputs[0][:2] == (34, 9)
  finally:
    client.close()
    phone.join()


@pytest.mark.parametrize('options', [{'loaded': False}, {'bad_spec': True}])
def test_wrong_or_unprepared_model_is_rejected_before_any_frame(options):
  phone = Phone(**options)
  client = phone.client()
  try:
    with pytest.raises(LinkError):
      client.prepare()
    assert phone.inputs == []
  finally:
    client.close()
    phone.join()


@pytest.mark.parametrize('reply', ['nan', 'wrong_frame', 'failed', 'short', 'timeout'])
def test_invalid_failed_or_late_reply_is_rejected(reply):
  phone = Phone(reply=reply)
  client = phone.client()
  try:
    client.prepare()
    started = time.monotonic()
    with pytest.raises(LinkError):
      client.infer(2, bytes(WARPED_BYTES), np.zeros(59), timeout=0.05)
    assert time.monotonic() - started < 0.3
  finally:
    client.close()
    phone.join()


def test_navigation_input_contains_only_current_fresh_command_and_distance():
  for kind, direction, command in [('turn', -1, 15), ('turn', 1, 10), ('uturn', -1, 13), ('fork', 1, 23)]:
    state = dict(fresh=True, kind=kind, direction=direction, distance=250)
    nav = navigation_input(state)
    assert nav[command] == 1 and nav[46] == 2.5
    assert navigation_input(dict(state, fresh=False)).sum() == 0
    assert navigation_input(dict(state, off_route=True)).sum() == 0
  assert navigation_input(dict(fresh=True, kind='turn', direction=1, distance=np.inf)).sum() == 0
