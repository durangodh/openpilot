"""기존 기록 파일(long_trace / lat_trace CSV) 내려받기 페이지.

CSV 기록 프로세스는 제거되었으며 이 페이지는 과거 파일만 제공합니다.

carrot_navi_server(포트 7714)가 WebSocket 이 아닌 일반 GET /trace 요청을 여기로 넘긴다.
폰 브라우저에서 http://EON_IP:7714/trace 를 열면 목록이 나오고, 누르면 받는다.
두 기록 폴더의 *.csv 만 내려준다(경로 이동 불가). '전체 삭제' 버튼(/trace?delete=all)은
같은 파일들을 지우되, 지금 기록 중인 파일(최근 RECORDING_S 초 안에 바뀐 것)은 남긴다.
"""
import html
import os
import re
import time
from urllib.parse import unquote

TRACE_DIRS = {
  # 제거된 기록기가 사용했던 경로. 기존 CSV 다운로드를 위해 유지한다.
  "long": ("가감속 (LONG TRACE)", "/data/media/0/long_trace"),
  "lat": ("조향 (LAT TRACE)", "/data/media/0/lat_trace"),
}
NAME_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}\.(csv|log)$")
CHUNK = 64 * 1024
RECORDING_S = 10.0


def is_trace_path(path):
  return path == "/trace" or path.startswith("/trace/") or path.startswith("/trace?")


def _send(conn, status, ctype, body, extra=""):
  head = ("HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
          "Cache-Control: no-store\r\nConnection: close\r\n%s\r\n") % (status, ctype, len(body), extra)
  conn.sendall(head.encode("utf-8") + body)


def _listing(deleted=None):
  parts = ["<!doctype html><meta charset=utf-8>",
           "<meta name=viewport content='width=device-width,initial-scale=1'>",
           "<title>EON 기록 파일</title>",
           "<style>body{font-family:sans-serif;margin:16px;background:#fff;color:#111}"
           "h2{margin-top:24px}a{display:block;padding:12px;margin:6px 0;border:1px solid #ccc;"
           "border-radius:8px;text-decoration:none;color:#0645ad}span{color:#555;font-size:90%}"
           "@media(prefers-color-scheme:dark){body{background:#111;color:#eee}a{color:#8ab4f8;border-color:#444}"
           "span{color:#aaa}}button{padding:12px 16px;font-size:100%;border-radius:8px;"
           "border:1px solid #c00;background:#fee;color:#c00}</style>",
           "<h1>EON 기록 파일</h1>",
           "<p>파일을 누르면 폰의 다운로드 폴더에 저장됩니다.</p>",
           "<button onclick=\"if(confirm('기록 파일을 모두 지울까요? 지금 기록 중인 파일은 남습니다.'))"
           "location.href='/trace?delete=all'\">전체 삭제</button>"]
  if deleted is not None:
    parts.append("<p>%d개 삭제했습니다.</p>" % deleted)
  for key, (title, directory) in TRACE_DIRS.items():
    parts.append("<h2>%s</h2>" % html.escape(title))
    try:
      names = [n for n in os.listdir(directory) if NAME_RE.match(n)]
    except OSError:
      names = []
    files = []
    for n in names:
      try:
        st = os.stat(os.path.join(directory, n))
        files.append((st.st_mtime, st.st_size, n))
      except OSError:
        pass
    if not files:
      parts.append("<p>저장된 기록 파일이 없습니다.</p>")
      continue
    for mtime, size, n in sorted(files, reverse=True):
      when = time.strftime("%m-%d %H:%M", time.localtime(mtime))
      parts.append("<a href='/trace/%s/%s'>%s <span>(%s 까지, %.0f KB)</span></a>"
                   % (key, html.escape(n), html.escape(n), when, size / 1024.0))
  return "".join(parts).encode("utf-8")


def _delete_all():
  """두 기록 폴더의 기록 파일을 지운다. 지금 기록 중인 파일은 남긴다."""
  now = time.time()
  count = 0
  for _, directory in TRACE_DIRS.values():
    try:
      names = [n for n in os.listdir(directory) if NAME_RE.match(n)]
    except OSError:
      continue
    for n in names:
      full = os.path.join(directory, n)
      try:
        if now - os.path.getmtime(full) < RECORDING_S:
          continue
        os.remove(full)
        count += 1
      except OSError:
        pass
  return count


def serve(conn, path):
  """GET /trace, /trace?delete=all 또는 /trace/<long|lat>/<이름>.csv 에 답한다."""
  query = path.split("?", 1)[1] if "?" in path else ""
  path = unquote(path.split("?", 1)[0]).rstrip("/")
  if path == "/trace":
    if query == "delete=all":
      deleted = _delete_all()
      # 새로고침으로 다시 지우지 않게 결과만 보여 주고 주소는 /trace 로 되돌린다.
      body = _listing(deleted) + b"<script>history.replaceState(null,'','/trace')</script>"
      _send(conn, "200 OK", "text/html; charset=utf-8", body)
      return
    _send(conn, "200 OK", "text/html; charset=utf-8", _listing())
    return
  parts = path.split("/")   # ['', 'trace', kind, name]
  if len(parts) == 4 and parts[2] in TRACE_DIRS and NAME_RE.match(parts[3]):
    full = os.path.join(TRACE_DIRS[parts[2]][1], parts[3])
    try:
      size = os.path.getsize(full)
      f = open(full, "rb")
    except OSError:
      _send(conn, "404 Not Found", "text/plain; charset=utf-8", "파일이 없습니다.".encode("utf-8"))
      return
    with f:
      download = "%s_%s" % (parts[2], parts[3])
      ctype = "text/csv" if parts[3].endswith(".csv") else "text/plain"
      head = ("HTTP/1.1 200 OK\r\nContent-Type: %s; charset=utf-8\r\nContent-Length: %d\r\n"
              "Content-Disposition: attachment; filename=\"%s\"\r\n"
              "Cache-Control: no-store\r\nConnection: close\r\n\r\n") % (ctype, size, download)
      conn.sendall(head.encode("ascii"))
      # 기록 중인 파일은 보내는 동안에도 커진다. 머리에 적은 크기만큼만 보낸다.
      left = size
      while left > 0:
        chunk = f.read(min(CHUNK, left))
        if not chunk:
          break
        conn.sendall(chunk)
        left -= len(chunk)
    return
  _send(conn, "404 Not Found", "text/plain; charset=utf-8", "없는 주소입니다.".encode("utf-8"))
