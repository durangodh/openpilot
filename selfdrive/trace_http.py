"""기록 파일(long_trace CSV) 내려받기 페이지.

carrot_navi_server(포트 7714)가 WebSocket 이 아닌 일반 GET /trace 요청을 여기로 넘긴다.
폰 브라우저에서 http://EON_IP:7714/trace 를 열면 목록이 나오고, 누르면 받는다.
기록 폴더의 *.csv / *.log 만 내려준다(경로 이동 불가). '전체 삭제' 버튼(/trace?delete=all)은
같은 파일들을 지우되, 지금 기록 중인 파일(최근 RECORDING_S 초 안에 바뀐 것)은 남긴다.
HUD 앱(S9/S20)은 자기 진단 로그를 POST /trace/upload/hud/<이름>.log 로 올린다.
"""
import html
import os
import re
import time
from urllib.parse import unquote

TRACE_DIRS = {
  # long_trace.TRACE_DIR 와 같아야 한다.
  "long": ("가감속 (LONG TRACE)", "/data/media/0/long_trace"),
  "hud": ("HUD 앱 로그 (휴대폰)", "/data/media/0/hud_log"),
  # selfdrive/debug/mcan_logger.py 의 LOG_DIR 와 같아야 한다.
  "mcan": ("M-CAN 로그 (버스 1)", "/data/media/0/mcan_logs"),
}
EMPTY_MSG = {
  "hud": "파일이 없습니다. HUD 앱이 EON 과 연결되면 1분 안에 올라옵니다.",
  "mcan": "파일이 없습니다. SSH 에서 python3 selfdrive/debug/mcan_logger.py 로 기록하면 생깁니다.",
}
# HUD 앱이 올릴 수 있는 파일. HudDiagnostics 는 512KB 에서 previous 로 넘긴다.
HUD_UPLOAD_NAMES = ("hud-session.log", "hud-session.previous.log", "naver-hud.log",
                    "tmap-hud.log", "kakao-hud.log")
HUD_UPLOAD_MAX = 1024 * 1024
NAME_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}\.(csv|log)$")
CHUNK = 64 * 1024
RECORDING_S = 10.0


def is_trace_path(path):
  return path == "/trace" or path.startswith("/trace/") or path.startswith("/trace?")


def is_upload_path(path):
  return path.startswith("/trace/upload/")


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
      parts.append("<p>%s</p>" % EMPTY_MSG.get(key, "파일이 없습니다. 설정에서 기록을 켜고 주행하면 생깁니다."))
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


def receive(conn, path, headers, body):
  """POST /trace/upload/hud/<이름>: HUD 앱 진단 로그를 받아 hud 폴더에 통째로 바꿔 쓴다."""
  parts = unquote(path.split("?", 1)[0]).rstrip("/").split("/")   # ['', 'trace', 'upload', 'hud', name]
  try:
    length = int(headers.get("content-length", "-1"))
  except ValueError:
    length = -1
  if len(parts) != 5 or parts[3] != "hud" or parts[4] not in HUD_UPLOAD_NAMES \
     or not 0 <= length <= HUD_UPLOAD_MAX:
    _send(conn, "400 Bad Request", "text/plain; charset=utf-8", b"bad upload")
    return
  while len(body) < length:
    part = conn.recv(min(CHUNK, length - len(body)))
    if not part:
      return
    body += part
  directory = TRACE_DIRS["hud"][1]
  try:
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, "." + parts[4] + ".tmp")
    with open(tmp, "wb") as f:
      f.write(body[:length])
    os.replace(tmp, os.path.join(directory, parts[4]))
  except OSError:
    _send(conn, "500 Internal Server Error", "text/plain; charset=utf-8", b"write failed")
    return
  _send(conn, "200 OK", "text/plain; charset=utf-8", b"ok")


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
