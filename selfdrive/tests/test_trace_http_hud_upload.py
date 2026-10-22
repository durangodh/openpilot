#!/usr/bin/env python3
"""HUD 앱 진단 로그 업로드(POST /trace/upload/hud/<이름>)와 /trace 목록·내려받기."""
import socket
import threading

from selfdrive import trace_http


def _read_all(sock):
  out = b""
  while True:
    part = sock.recv(65536)
    if not part:
      return out
    out += part


def _call(fn, *args):
  a, b = socket.socketpair()
  reader = []
  t = threading.Thread(target=lambda: reader.append(_read_all(b)), daemon=True)
  t.start()
  try:
    fn(a, *args)
    a.shutdown(socket.SHUT_WR)
    t.join(10)
    return reader[0]
  finally:
    a.close()
    b.close()


def _upload(name, body, first=None):
  a, b = socket.socketpair()
  first = body if first is None else first

  def feed():
    try:
      b.sendall(body[len(first):])
      b.shutdown(socket.SHUT_WR)
    except OSError:
      pass   # 서버가 크기 초과로 읽지 않고 닫은 경우
  sender = threading.Thread(target=feed, daemon=True)
  sender.start()
  try:
    trace_http.receive(a, "/trace/upload/hud/" + name, {"content-length": str(len(body))}, first)
    a.shutdown(socket.SHUT_WR)
    reply = b""
    while b"\r\n" not in reply:
      part = b.recv(4096)
      if not part:
        break
      reply += part
    return reply.split(b"\r\n")[0]
  finally:
    a.close()
    b.close()


def test_upload_list_and_download(tmp_path, monkeypatch):
  monkeypatch.setitem(trace_http.TRACE_DIRS, "hud", ("HUD", str(tmp_path)))
  body = b"usb-error x\n" * 5000
  assert _upload("hud-session.log", body, first=body[:100]) == b"HTTP/1.1 200 OK"
  assert (tmp_path / "hud-session.log").read_bytes() == body
  assert "/trace/hud/hud-session.log" in _call(trace_http.serve, "/trace").decode()
  assert _call(trace_http.serve, "/trace/hud/hud-session.log").split(b"\r\n\r\n", 1)[1] == body


def test_upload_rejects_other_names_and_oversize(tmp_path, monkeypatch):
  monkeypatch.setitem(trace_http.TRACE_DIRS, "hud", ("HUD", str(tmp_path)))
  assert _upload("../x.log", b"abc") == b"HTTP/1.1 400 Bad Request"
  assert _upload("evil.log", b"abc") == b"HTTP/1.1 400 Bad Request"
  big = b"x" * (trace_http.HUD_UPLOAD_MAX + 1)
  assert _upload("hud-session.log", big, first=b"") == b"HTTP/1.1 400 Bad Request"
  assert not any(tmp_path.iterdir())
