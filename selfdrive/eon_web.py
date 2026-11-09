"""Local, read-only settings and route log access for EON."""

import base64
import hmac
import json
import os
import re
import secrets
import shutil
import stat
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from common.params import Params, UnknownKeyName
from selfdrive.loggerd.config import ROOT

PORT = 7000
PASSWORD_FILE = "/persist/comma/eon-web-password"
SEGMENT = re.compile(r"\d{4}-\d{2}-\d{2}--\d{2}-\d{2}-\d{2}--\d+")
LOG_FILES = ("rlog.bz2", "qlog.bz2")
PARAM_KEYS = (
  "CruiseSpeedMin", "TFollowSpeedRatio", "TFollowGap1", "TFollowGap2",
  "TFollowGap3", "TFollowGap4", "NoLeadCruiseAccelFactor", "LeadCruiseAccelFactor",
  "LongTuningKpV", "LongTuningKiV", "LongTuningKf", "LongitudinalActuatorDelayLowerBound",
  "SteeringRateCost", "SteerActuatorDelay", "SteerRatioApply", "LiveSteerRatioApply",
  "LateralTorqueCustom", "LateralTorqueAccelFactor", "LateralTorqueFriction",
  "EonClusterHudFps", "EonClusterHudMapFps", "EonClusterHudJpegQuality",
  "EonClusterHudNavApp", "EonClusterHudNaviLayout", "EonClusterHudMapScaleTmap",
  "EonClusterHudMapScaleNaver", "EonClusterHudMapScaleKakao",
  "LongTraceEnabled",
)

PAGE = """<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EON 웹</title>
<style>
body{font:16px system-ui,sans-serif;background:#111923;color:#eef4fa;max-width:760px;margin:auto;padding:16px}
section{background:#1d2835;border-radius:12px;padding:16px;margin:16px 0}
h1,h2{margin:0 0 12px} a{color:#91ddff} input,button{font:inherit;padding:8px;max-width:100%}
ul{padding:0;list-style:none} li{padding:9px 0;border-bottom:1px solid #374352;overflow-wrap:anywhere}
.log a{display:inline-block;margin:6px 16px 0 0}
</style>
<h1>EON 웹</h1><p>설정 조회와 주행 로그 다운로드</p>
<section><h2>설정</h2><input id="filter" type="search" placeholder="파람 이름 검색" aria-label="파람 이름 검색">
<a href="/api/params.json" download="g_web-params.json">JSON 백업</a><ul id="params"></ul></section>
<section><h2>완료된 주행 로그</h2><button id="refresh">새로고침</button><ul id="logs"></ul></section>
<script>
const paramsList = document.querySelector('#params');
const logsList = document.querySelector('#logs');
const filter = document.querySelector('#filter');
let params = {};
function row(list, label) {
  const item = document.createElement('li');
  item.textContent = label;
  list.append(item);
  return item;
}
async function read(path) {
  const response = await fetch(path);
  if (!response.ok) throw Error(response.status);
  return response.json();
}
function renderParams() {
  paramsList.replaceChildren();
  const query = filter.value.toLowerCase().trim();
  for (const [key, value] of Object.entries(params)) {
    if (key.toLowerCase().includes(query)) row(paramsList, key + ' = ' + (value ?? '미설정'));
  }
}
async function loadLogs() {
  logsList.replaceChildren();
  try {
    const segments = await read('/api/logs.json');
    if (!segments.length) row(logsList, '완료된 주행 로그가 없습니다.');
    for (const segment of segments) {
      const item = row(logsList, segment.name);
      item.className = 'log';
      for (const filename of segment.files) {
        const link = document.createElement('a');
        link.href = '/logs/' + segment.name + '/' + filename;
        link.textContent = filename;
        item.append(link);
      }
    }
  } catch (_) { row(logsList, '로그 목록을 불러오지 못했습니다.'); }
}
filter.addEventListener('input', renderParams);
document.querySelector('#refresh').addEventListener('click', loadLogs);
read('/api/params.json').then(data => { params = data.params; renderParams(); })
  .catch(() => row(paramsList, '설정을 불러오지 못했습니다.'));
loadLogs();
</script></html>
""".encode("utf-8")


def password():
  try:
    fd = os.open(PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
  except FileExistsError:
    return Path(PASSWORD_FILE).read_text().strip()
  value = secrets.token_urlsafe(18)
  with os.fdopen(fd, "w") as output:
    output.write(value + "\n")
  return value


def settings_snapshot():
  params = Params()
  values = {}
  for key in PARAM_KEYS:
    try:
      values[key] = params.get(key, encoding="utf-8")
    except (UnknownKeyName, UnicodeDecodeError):
      continue
  return {"branch": "g_web", "params": values}


def segments():
  try:
    with os.scandir(ROOT) as entries:
      names = [entry.name for entry in entries if SEGMENT.fullmatch(entry.name)
               and entry.is_dir(follow_symlinks=False)]
  except FileNotFoundError:
    return []
  result = []
  for name in sorted(names, key=lambda name: (name.rsplit("--", 1)[0], int(name.rsplit("--", 1)[1])), reverse=True):
    directory = Path(ROOT) / name
    if (directory / "rlog.bz2.lock").exists():
      continue
    files = [filename for filename in LOG_FILES
             if not (directory / filename).is_symlink() and (directory / filename).is_file()]
    if files:
      result.append({"name": name, "files": files})
    if len(result) == 120:
      break
  return result


def open_log(segment, filename):
  if not SEGMENT.fullmatch(segment) or filename not in LOG_FILES:
    raise FileNotFoundError
  directory = os.open(os.path.join(ROOT, segment), os.O_DIRECTORY | os.O_NOFOLLOW)
  try:
    if "rlog.bz2.lock" in os.listdir(directory):
      raise FileNotFoundError
    fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    with_fd = os.fdopen(fd, "rb")
    if not stat.S_ISREG(os.fstat(fd).st_mode):
      with_fd.close()
      raise FileNotFoundError
    return with_fd
  finally:
    os.close(directory)


class Handler(BaseHTTPRequestHandler):
  timeout = 5

  def log_message(self, *_args):
    pass

  def begin(self, status, mime, size, attachment=None):
    self.send_response(status)
    self.send_header("Content-Type", mime)
    self.send_header("Content-Length", str(size))
    self.send_header("Cache-Control", "no-store")
    self.send_header("X-Content-Type-Options", "nosniff")
    self.send_header("X-Frame-Options", "DENY")
    if attachment:
      self.send_header("Content-Disposition", 'attachment; filename="{}"'.format(attachment))
    self.end_headers()

  def send_data(self, body, mime):
    self.begin(200, mime, len(body))
    self.wfile.write(body)

  def send_json(self, data):
    self.send_data(json.dumps(data).encode("utf-8"), "application/json")

  def do_GET(self):
    auth = self.headers.get("Authorization", "").encode("utf-8")
    if not hmac.compare_digest(auth, self.server.authorization):
      self.send_response(401)
      self.send_header("WWW-Authenticate", 'Basic realm="EON Web"')
      self.send_header("Content-Length", "0")
      self.end_headers()
      return
    path = urlsplit(self.path).path
    if path == "/":
      self.send_data(PAGE, "text/html; charset=utf-8")
    elif path == "/api/params.json":
      self.send_json(settings_snapshot())
    elif path == "/api/logs.json":
      self.send_json(segments())
    elif path.startswith("/logs/") and len(path.split("/")) == 4:
      _, _, segment, filename = path.split("/")
      try:
        source = open_log(segment, filename)
      except OSError:
        self.send_error(404)
        return
      with source:
        self.begin(200, "application/octet-stream", os.fstat(source.fileno()).st_size,
                   "{}-{}".format(segment, filename))
        shutil.copyfileobj(source, self.wfile, length=64 * 1024)
    else:
      self.send_error(404)


def main():
  value = password()
  if not value:
    raise RuntimeError("EON web password file is empty")
  with HTTPServer(("0.0.0.0", PORT), Handler) as server:
    server.authorization = b"Basic " + base64.b64encode(("eon:" + value).encode("utf-8"))
    server.serve_forever()


if __name__ == "__main__":
  main()
