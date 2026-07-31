#!/usr/bin/env python3
"""Small CarrotNavi/Tmap V2 receiver for the g_navi EON branch.

The modified Tmap APK discovers this process over UDP/7705 and opens RFC6455
WebSockets on TCP/7714.  Received JSON is exposed to the Qt UI through a small
atomic file in /dev/shm, avoiding cereal changes and Mapbox dependencies.
"""

import base64
import errno
import hashlib
import json
import os
import socket
import struct
import threading
import time
import uuid


PORT = 7714
DISCOVERY_PORT = 7705
STATE_FILE = "/dev/shm/carrot_navi_route.json"
JSON_NAMES = (
  "vehicle", "guidance_current", "guidance_next", "lane_current", "lane_ahead",
  "speed", "traffic_signal", "crossroad", "route", "navigation_status",
  "app_status", "camera_state", "composition_state",
)
IMAGE_NAMES = (
  "tbt_current_compact", "tbt_current_full", "tbt_next", "traffic_signal",
  "lane_top", "lane_bottom", "safety_primary", "safety_secondary",
  "safety_section", "crossroad_minimized", "crossroad_expanded",
  "center_tbt_icon", "center_tbt_text", "center_tbt_fee",
)
RENDER_NAMES = ("map_main",)
ENABLED = {"vehicle", "guidance_current", "guidance_next", "route", "navigation_status"}
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class NaviState(object):
  def __init__(self):
    self.lock = threading.Lock()
    self.values = {}
    self.updated_at = {}

  def update(self, name, value):
    if name not in JSON_NAMES:
      return
    with self.lock:
      self.values[name] = value
      now_ms = int(time.time() * 1000)
      self.updated_at[name] = now_ms
      output = dict(self.values)
      output["updated_at_ms"] = now_ms
      output["stream_updated_at_ms"] = dict(self.updated_at)
    tmp = STATE_FILE + ".tmp"
    try:
      with open(tmp, "w") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
      os.rename(tmp, STATE_FILE)
    except IOError:
      pass


def local_ip():
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  try:
    sock.connect(("8.8.8.8", 80))
    return sock.getsockname()[0]
  except socket.error:
    return "127.0.0.1"
  finally:
    sock.close()


def recv_exact(sock, length):
  data = b""
  while len(data) < length:
    part = sock.recv(length - len(data))
    if not part:
      raise EOFError()
    data += part
  return data


def recv_frame(sock):
  header = recv_exact(sock, 2)
  first, second = struct.unpack("!BB", header)
  opcode = first & 0x0f
  length = second & 0x7f
  if length == 126:
    length = struct.unpack("!H", recv_exact(sock, 2))[0]
  elif length == 127:
    length = struct.unpack("!Q", recv_exact(sock, 8))[0]
  mask = recv_exact(sock, 4) if second & 0x80 else None
  payload = recv_exact(sock, length)
  if mask:
    payload = bytes(bytearray(v ^ mask[i % 4] for i, v in enumerate(bytearray(payload))))
  return opcode, payload


def send_frame(sock, payload, opcode=1):
  if isinstance(payload, str):
    payload = payload.encode("utf-8")
  length = len(payload)
  header = struct.pack("!B", 0x80 | opcode)
  if length < 126:
    header += struct.pack("!B", length)
  elif length < 65536:
    header += struct.pack("!BH", 126, length)
  else:
    header += struct.pack("!BQ", 127, length)
  sock.sendall(header + payload)


def manifest():
  streams = []
  handle = 1
  for kind, names in (("json", JSON_NAMES), ("image", IMAGE_NAMES), ("render", RENDER_NAMES)):
    for name in names:
      enabled = kind == "json" and name in ENABLED
      params = {}
      if kind == "json":
        params = {"delivery_mode": "on_change_with_heartbeat", "interval_ms": 500,
                  "stale_timeout_ms": 30000}
      elif kind == "image":
        params = {"format": "png", "max_fps": 1}
      else:
        params = {"width": 480, "height": 360, "dpi": 160, "fps": 1,
                  "codec": "jpeg", "h264_bitrate_kbps": 500,
                  "camera_mode": "app_sync", "map_theme": "dark"}
      streams.append({"kind": kind, "name": name, "schema_version": 1,
                      "stream_handle": handle, "enabled": enabled, "params": params})
      handle += 1
  return {"type": "subscription_manifest", "protocol_version": 2,
          "session_id": uuid.uuid4().hex, "revision": 1,
          "metrics_enabled": False, "streams": streams}


def websocket_handshake(conn):
  request = b""
  while b"\r\n\r\n" not in request and len(request) < 16384:
    part = conn.recv(4096)
    if not part:
      raise EOFError()
    request += part
  lines = request.decode("iso-8859-1").split("\r\n")
  path = lines[0].split(" ")[1]
  headers = {}
  for line in lines[1:]:
    if ":" in line:
      key, value = line.split(":", 1)
      headers[key.strip().lower()] = value.strip()
  key = headers.get("sec-websocket-key", "")
  if not key:
    raise ValueError("missing websocket key")
  accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode("ascii")).digest()).decode("ascii")
  response = ("HTTP/1.1 101 Switching Protocols\r\n"
              "Upgrade: websocket\r\nConnection: Upgrade\r\n"
              "Sec-WebSocket-Accept: %s\r\n\r\n") % accept
  conn.sendall(response.encode("ascii"))
  return path


def client_loop(conn, state):
  path = websocket_handshake(conn)
  is_control = "/control/" in path
  stream_name = path.rstrip("/").split("/")[-1] if not is_control else None
  while True:
    opcode, payload = recv_frame(conn)
    if opcode == 8:
      send_frame(conn, b"", 8)
      return
    if opcode == 9:
      send_frame(conn, payload, 10)
      continue
    if opcode != 1:
      continue
    try:
      message = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
      continue
    if is_control and message.get("type") == "requirements_query":
      send_frame(conn, json.dumps(manifest(), separators=(",", ":")))
    elif not is_control and message.get("type") == "item_update":
      name = message.get("name", stream_name)
      state.update(name, message.get("value") if message.get("present", True) else None)


def handle_client(conn, state):
  try:
    conn.settimeout(45)
    client_loop(conn, state)
  except (EOFError, IOError, ValueError, socket.error):
    pass
  finally:
    try:
      conn.close()
    except socket.error:
      pass


def discovery_loop():
  sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
  sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
  payload_ip = None
  while True:
    ip = local_ip()
    if ip != "127.0.0.1":
      payload_ip = ip
    if payload_ip:
      payload = json.dumps({"ip": payload_ip, "navi_debug": 0},
                           separators=(",", ":")).encode("utf-8")
      try:
        sock.sendto(payload, ("255.255.255.255", DISCOVERY_PORT))
      except socket.error as e:
        if e.errno not in (errno.ENETUNREACH, errno.EADDRNOTAVAIL):
          pass
    time.sleep(1)


def main():
  state = NaviState()
  threading.Thread(target=discovery_loop, name="carrot-navi-discovery", daemon=True).start()
  server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
  server.bind(("0.0.0.0", PORT))
  server.listen(16)
  while True:
    conn, _ = server.accept()
    threading.Thread(target=handle_client, args=(conn, state), daemon=True).start()


if __name__ == "__main__":
  main()
