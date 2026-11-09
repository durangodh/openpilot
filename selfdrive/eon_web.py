"""Local settings, route logs and maintenance for EON."""

import base64
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from common.basedir import BASEDIR
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
)

PAGE = """<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EON 웹</title>
<style>
body{font:16px system-ui,sans-serif;background:#111923;color:#eef4fa;max-width:760px;margin:auto;padding:16px}
nav{position:fixed;bottom:0;left:0;right:0;display:flex;background:#17212e;padding:6px;gap:4px}
nav button{flex:1;min-width:0;border:0;color:#eef4fa;background:none;border-radius:8px}
nav button[aria-selected=true]{background:#a84b12;color:white}
main{padding-bottom:62px}
pre{white-space:pre-wrap;overflow-wrap:anywhere}
details{padding:10px 0;border-bottom:1px solid #374352}
section{background:#1d2835;border-radius:12px;padding:16px;margin:16px 0}
h1,h2{margin:0 0 12px} a{color:#91ddff} input,button{font:inherit;padding:8px;max-width:100%}
ul{padding:0;list-style:none} li{padding:9px 0;border-bottom:1px solid #374352;overflow-wrap:anywhere}
.log a{display:inline-block;margin:6px 16px 0 0}
</style>
<main><h1>EON 웹</h1>
<section id="drive"><h2>주행</h2><button id="driveRefresh">장치 상태 새로고침</button>
<pre id="driveStatus"></pre><p>저장된 주행 기록은 로그 탭에서 확인할 수 있습니다.</p></section>
<section id="settings" hidden><h2>설정</h2><input id="filter" type="search" placeholder="파람 이름 검색" aria-label="파람 이름 검색">
<a href="/api/params.json" download="g_web-params.json">JSON 백업</a><ul id="params"></ul></section>
<section id="tools" hidden><h2>도구</h2>
<details open><summary>Git Commands</summary><p id="gitInfo"></p>
<button id="gitPull">git pull</button> <button id="gitLog">git log</button>
<pre id="gitOutput" aria-live="polite"></pre></details>
<details><summary>User / System</summary><button data-tmux>capture tmux</button></details></section>
<section id="records" hidden><h2>로그 · 대시캠</h2><button id="refresh">새로고침</button><div id="logs"></div></section>
<section id="terminal" hidden><h2>터미널 로그</h2><button data-tmux>capture tmux</button>
<pre id="tmuxOutput" aria-live="polite"></pre></section>
</main>
<nav aria-label="메뉴">
<button data-page="drive" aria-selected="true">주행</button><button data-page="settings">설정</button>
<button data-page="tools">도구</button><button data-page="records">로그</button><button data-page="terminal">터미널</button>
</nav>
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
    const routes = new Map();
    for (const segment of segments) {
      const split = segment.name.lastIndexOf('--');
      const route = segment.name.slice(0, split);
      if (!routes.has(route)) {
        const group = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = route.replace('--', ' ');
        group.append(summary);
        logsList.append(group);
        routes.set(route, group);
      }
      const item = document.createElement('div');
      item.textContent = '구간 ' + segment.name.slice(split + 2);
      item.className = 'log';
      routes.get(route).append(item);
      for (const filename of segment.files) {
        const link = document.createElement('a');
        link.href = '/logs/' + segment.name + '/' + filename;
        link.textContent = filename;
        item.append(link);
      }
    }
  } catch (_) { row(logsList, '로그 목록을 불러오지 못했습니다.'); }
}
async function loadSettings() {
  try { params = (await read('/api/params.json')).params; renderParams(); }
  catch (_) { paramsList.textContent = '설정을 불러오지 못했습니다.'; }
}
async function loadStatus() {
  const target = document.querySelector('#driveStatus');
  try {
    const state = await read('/api/device.json');
    target.textContent = (state.started ? '온로드' : '오프로드') + ' · 저장 공간 ' + state.freeSpace + '%'
      + (state.cpuTemp === null ? '' : ' · CPU ' + state.cpuTemp + ' °C');
  } catch (_) { target.textContent = 'EON 상태를 확인할 수 없습니다.'; }
}
const gitInfo = document.querySelector('#gitInfo');
const gitOutput = document.querySelector('#gitOutput');
async function loadGit() {
  try {
    const state = await read('/api/git.json');
    gitInfo.textContent = state.branch + ' · ' + state.commit;
  } catch (_) { gitInfo.textContent = 'Git 정보를 읽지 못했습니다.'; }
}
const loaders = {drive: loadStatus, settings: loadSettings, tools: loadGit, records: loadLogs};
function openPage(id) {
  for (const button of document.querySelectorAll('[data-page]')) {
    const selected = button.dataset.page === id;
    button.setAttribute('aria-selected', selected);
    document.getElementById(button.dataset.page).hidden = !selected;
  }
  if (loaders[id]) loaders[id]();
}
for (const button of document.querySelectorAll('[data-page]')) {
  button.addEventListener('click', () => openPage(button.dataset.page));
}
for (const button of document.querySelectorAll('[data-tmux]')) {
  button.addEventListener('click', async () => {
    openPage('terminal');
    const output = document.querySelector('#tmuxOutput');
    output.textContent = '읽는 중…';
    try { output.textContent = (await read('/api/tmux.json')).output; }
    catch (_) { output.textContent = 'tmux 로그를 읽지 못했습니다.'; }
  });
}
document.querySelector('#gitPull').addEventListener('click', async event => {
  if (!confirm('시동을 끈 상태에서 g_web 업데이트를 가져올까요?')) return;
  const button = event.currentTarget;
  button.disabled = true;
  gitOutput.textContent = '업데이트 확인 중…';
  try {
    const response = await fetch('/api/git/pull', {method: 'POST', headers: {'X-EON-Action': 'git-pull'}});
    gitOutput.textContent = (await response.json()).output;
    await loadGit();
  } catch (_) { gitOutput.textContent = '응답을 읽지 못했습니다. 현재 커밋을 확인하세요.'; }
  finally { button.disabled = false; }
});
document.querySelector('#gitLog').addEventListener('click', async () => {
  try { gitOutput.textContent = (await read('/api/git.json')).history; }
  catch (_) { gitOutput.textContent = '커밋 목록을 읽지 못했습니다.'; }
});
filter.addEventListener('input', renderParams);
document.querySelector('#driveRefresh').addEventListener('click', loadStatus);
document.querySelector('#refresh').addEventListener('click', loadLogs);
openPage('drive');
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


def command(args, timeout=5):
  result = subprocess.run(args, cwd=BASEDIR, timeout=timeout, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
  return result.returncode, result.stdout.decode("utf-8", "replace").strip()


def git_info():
  branch_status, branch = command(["git", "symbolic-ref", "--quiet", "--short", "HEAD"])
  commit_status, commit = command(["git", "rev-parse", "--short", "HEAD"])
  history_status, history = command(["git", "log", "-10", "--oneline", "--no-color"])
  if branch_status or commit_status or history_status:
    raise RuntimeError("Git 정보를 읽지 못했습니다.")
  return {"branch": branch, "commit": commit, "history": history}


def read_device_state(check_ignition=False):
  from cereal import messaging

  services = ["deviceState", "pandaStates"] if check_ignition else ["deviceState"]
  subscriber = messaging.SubMaster(services)
  seen = set()
  deadline = time.monotonic() + 3
  while time.monotonic() < deadline:
    subscriber.update(200)
    for service in services:
      if subscriber.updated[service] and subscriber.valid[service]:
        seen.add(service)
    if len(seen) == len(services):
      device = subscriber["deviceState"]
      temperatures = list(device.cpuTempC)
      state = {"started": device.started, "freeSpace": round(device.freeSpacePercent),
               "cpuTemp": round(max(temperatures)) if temperatures else None}
      if check_ignition:
        pandas = [panda for panda in subscriber["pandaStates"] if str(panda.pandaType) != "unknown"]
        state["ignitionOff"] = bool(pandas) and not any(p.ignitionLine or p.ignitionCan for p in pandas)
      return state
  raise RuntimeError("EON 또는 판다 상태를 확인하지 못했습니다.")


def git_pull():
  if git_info()["branch"] != "g_web":
    return False, "g_web 브랜치에서만 업데이트할 수 있습니다."
  status, changes = command(["git", "status", "--porcelain", "--untracked-files=no"])
  if status or changes:
    return False, "로컬 추적 파일에 변경이 있습니다. SSH에서 git status를 확인하세요."
  state = read_device_state(check_ignition=True)
  if state["started"] or not state["ignitionOff"]:
    return False, "시동을 끈 뒤 다시 시도하세요."
  status, output = command(["git", "fetch", "--no-recurse-submodules", "origin", "g_web"], timeout=35)
  if status:
    return False, output or "원격 브랜치를 가져오지 못했습니다."
  status, target = command(["git", "rev-parse", "--verify", "FETCH_HEAD"])
  if status:
    return False, target or "가져온 커밋을 확인하지 못했습니다."
  state = read_device_state(check_ignition=True)
  if state["started"] or not state["ignitionOff"]:
    return False, "시동 상태가 바뀌어 업데이트를 중단했습니다."
  before = git_info()["commit"]
  status, output = command(["git", "-c", "core.hooksPath=/dev/null", "merge", "--ff-only", target])
  if status:
    return False, output or "Git Pull에 실패했습니다."
  after = git_info()["commit"]
  detail = "다음 EON 재부팅부터 새 코드가 적용됩니다." if before != after else "이미 최신 커밋입니다."
  return True, "{} → {}\n{}\n{}".format(before, after, detail, output)


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

  def send_json(self, data, status=200):
    body = json.dumps(data).encode("utf-8")
    self.begin(status, "application/json", len(body))
    self.wfile.write(body)

  def authorized(self):
    auth = self.headers.get("Authorization", "").encode("utf-8")
    if not hmac.compare_digest(auth, self.server.authorization):
      self.send_response(401)
      self.send_header("WWW-Authenticate", 'Basic realm="EON Web"')
      self.send_header("Content-Length", "0")
      self.end_headers()
      return False
    return True

  def do_POST(self):
    if not self.authorized():
      return
    if self.path != "/api/git/pull":
      self.send_error(404)
      return
    if self.headers.get("X-EON-Action") != "git-pull" or self.headers.get("Sec-Fetch-Site") != "same-origin":
      self.send_json({"output": "EON 웹 화면에서 실행하세요."}, 403)
      return
    try:
      ok, output = git_pull()
      self.send_json({"output": output}, 200 if ok else 409)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
      self.send_json({"output": str(error)}, 503)

  def do_GET(self):
    if not self.authorized():
      return
    try:
      self.serve_get()
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
      if not isinstance(error, (BrokenPipeError, ConnectionResetError, TimeoutError)):
        self.send_json({"output": str(error)}, 503)

  def serve_get(self):
    path = urlsplit(self.path).path
    if path == "/":
      self.send_data(PAGE, "text/html; charset=utf-8")
    elif path == "/api/git.json":
      self.send_json(git_info())
    elif path == "/api/device.json":
      self.send_json(read_device_state())
    elif path == "/api/tmux.json":
      status, output = command(["tmux", "capture-pane", "-p", "-S", "-300"])
      self.send_json({"output": output}, 503 if status else 200)
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
