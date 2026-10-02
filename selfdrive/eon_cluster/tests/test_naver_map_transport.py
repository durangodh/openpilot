#!/usr/bin/env python3
"""Exercise Naver map frames through the real WebSocket receiver."""
import base64
import json
import socket
import struct
import sys
import threading
import types
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
try:
  import common.params  # noqa: F401,E402
except ImportError:
  # Only without native Params. Replacing a working module here leaked a
  # Params=object stub into every test collected after this file.
  params_module = types.ModuleType("common.params")
  params_module.Params = object
  sys.modules["common.params"] = params_module

from selfdrive import carrot_navi_server as server  # noqa: E402


class State(object):
  def __init__(self):
    self.maps = []
    self.overlays = []

  def update_map(self, source, payload):
    self.maps.append((source, payload))

  def update_overlay(self, *args):
    self.overlays.append(args)

  def update(self, *args):
    pass

  def accepts(self, source):
    return source == server.SOURCE_NAVER

  def clear_map(self):
    pass

  def clear_overlay(self, *args):
    pass

  def control_disconnected(self, *args):
    pass


def masked_frame(opcode, payload):
  mask = b"NH14"
  length = len(payload)
  if length < 126:
    header = bytes((0x80 | opcode, 0x80 | length))
  elif length < 65536:
    header = bytes((0x80 | opcode, 0xfe)) + struct.pack("!H", length)
  else:
    header = bytes((0x80 | opcode, 0xff)) + struct.pack("!Q", length)
  encoded = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
  return header + mask + encoded


def main():
  receiver, client = socket.socketpair()
  state = State()
  thread = threading.Thread(target=server.client_loop, args=(receiver, state))
  thread.start()
  client.sendall(
    b"GET /api/navi/ws/v2/json/naver/state HTTP/1.1\r\n"
    b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
  )
  assert client.recv(4096).startswith(b"HTTP/1.1 101")

  # HUD14: raw binary JPEG on the Naver state connection.
  jpeg = b"\xff\xd8" + b"N" * 400000 + b"\xff\xd9"
  client.sendall(masked_frame(2, jpeg))

  # Keep the old Base64 JSON form compatible with HUD3-HUD13.
  legacy = json.dumps({
    "type": "item_update", "name": "map_main", "present": True,
    "value": {"format": "jpeg", "data": base64.b64encode(jpeg).decode("ascii")},
  }, separators=(",", ":")).encode("utf-8")
  client.sendall(masked_frame(1, legacy))
  client.sendall(masked_frame(8, b""))
  thread.join(5)
  client.close()

  assert not thread.is_alive()
  assert state.maps == [(server.SOURCE_NAVER, jpeg), (server.SOURCE_NAVER, jpeg)]
  assert state.overlays == []
  print("PASS: HUD14 binary and legacy Base64 Naver map frames")

  # Named overlay sockets are overlays for NAVER too, never map frames.
  for path, name in ((b"/api/navi/ws/v2/image/naver/crossroad_expanded", "crossroad_expanded"),
                     (b"/api/navi/ws/v2/render/naver/traffic_signal", "traffic_signal")):
    receiver, client = socket.socketpair()
    state = State()
    thread = threading.Thread(target=server.client_loop, args=(receiver, state))
    thread.start()
    client.sendall(b"GET " + path + b" HTTP/1.1\r\n"
                   b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")
    assert client.recv(4096).startswith(b"HTTP/1.1 101")
    client.sendall(masked_frame(2, jpeg))
    client.sendall(masked_frame(8, b""))
    thread.join(5)
    client.close()
    assert not thread.is_alive()
    assert state.maps == []
    assert state.overlays == [(server.SOURCE_NAVER, name, jpeg)]
  print("PASS: NAVER overlay sockets go to overlays, not map_main")


if __name__ == "__main__":
  main()
