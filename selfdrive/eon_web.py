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
from selfdrive.eon_web_media import RoadPreview, VIDEO_FILE, media_range, replay_data
from selfdrive.loggerd.config import ROOT

PORT = 7000
ICON_FILES = {
  "/eon-web-icon-192.png": "eon-web-icon-192.png",
  "/eon-web-icon-512.png": "eon-web-icon-512.png",
}
WEB_MANIFEST = {
  "name": "EON 웹",
  "short_name": "EON 웹",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#000000",
  "theme_color": "#000000",
  "icons": [
    {"src": "/eon-web-icon-192.png", "sizes": "192x192", "type": "image/png"},
    {"src": "/eon-web-icon-512.png", "sizes": "512x512", "type": "image/png"},
  ],
}
REBUILD_FLAG = Path("/persist/comma/eon-web-rebuild")
RECORDING_FLAGS = {
  "routeLogging": Path("/persist/comma/enable-eon-route-logging"),
  "webVideo": Path("/persist/comma/enable-eon-web-video"),
}
PASSWORD_FILE = "/persist/comma/eon-web-password"
SEGMENT = re.compile(r"\d{4}-\d{2}-\d{2}--\d{2}-\d{2}-\d{2}--\d+")
LOG_FILES = ("rlog.bz2", "qlog.bz2", VIDEO_FILE)
PAGE = r"""<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>EON 웹</title>
<link rel="icon" type="image/png" sizes="192x192" href="/eon-web-icon-192.png">
<link rel="apple-touch-icon" sizes="192x192" href="/eon-web-icon-192.png">
<link rel="manifest" href="/eon-web.webmanifest">
<style>
:root{color-scheme:dark;--bg:#10161e;--panel:#141d26;--card:#18212b;--border:#35414d;--text:#eff3f7;--muted:#bec9d3;--accent:#f0ac73;--active:#402e23}
*{box-sizing:border-box}
body{font:16px system-ui,sans-serif;background:var(--bg);color:var(--text);max-width:1060px;margin:auto;padding:clamp(10px,2.3vw,22px)}
main{min-width:0;padding-bottom:calc(70px + env(safe-area-inset-bottom))}
.page-header{display:flex;justify-content:space-between;align-items:center;gap:8px}
.page-header h1{margin:0}
#webSettings{width:min(440px,calc(100% - 24px));color:var(--text);background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:16px}
#webSettings::backdrop{background:#070a10b8}
#webSettings select{display:block;width:100%;margin-top:8px}
.dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
.dialog-actions button[type=submit]{background:var(--active);border-color:var(--accent);color:var(--accent)}
main>section{background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:clamp(10px,2vw,18px);margin:10px 0}
h1,h2,h3{margin:0 0 12px} h1{font-size:1.4rem} h2{font-size:1.2rem} h3{font-size:1.05rem}
p{margin:8px 0} a{color:var(--accent)}
button,input,select{font:inherit;color:inherit;max-width:100%;min-height:42px;padding:8px 11px;border:1px solid var(--border);border-radius:9px;background:var(--card)}
button{cursor:pointer} button:disabled{cursor:default;opacity:.5}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
nav{position:fixed;bottom:0;left:0;right:0;z-index:2;display:flex;justify-content:center;background:var(--panel);border-top:1px solid var(--border);padding:6px 6px calc(6px + env(safe-area-inset-bottom));gap:4px}
nav button{flex:1;max-width:200px;min-width:0;padding-inline:3px;border-color:transparent;background:none;font-size:.9rem}
button[aria-selected=true],button[aria-pressed=true],.param-editor button[type=submit]{background:var(--active);color:var(--accent);border-color:var(--accent)}
[hidden]{display:none!important}
pre{margin:8px 0;white-space:pre-wrap;overflow-wrap:anywhere}
details,.card,.param-row,.log{padding:11px;border:1px solid var(--border);border-radius:10px;background:var(--card);margin:8px 0;min-width:0}
summary{cursor:pointer;font-weight:650}
.actions,.settings-bar,.param-editor-controls,.param-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.settings-bar{margin-bottom:12px} #filter{flex:1;min-width:130px}
.settings-layout{display:grid;grid-template-columns:minmax(110px,155px) minmax(0,1fr);gap:12px}
#paramGroups{display:flex;flex-direction:column;gap:6px;min-width:0}
#paramGroups button{text-align:left}
.param-head{justify-content:space-between}
.param-head>span{flex:1;min-width:0;overflow-wrap:anywhere}
.param-value{color:var(--accent);font-variant-numeric:tabular-nums}
.param-editor,.param-description,.hint{font-size:.9rem;color:var(--muted)}
.param-description{white-space:pre-line;margin:6px 0 0;overflow-wrap:anywhere}
.param-meta{margin:5px 0 0;color:var(--muted);font-size:.8rem;overflow-wrap:anywhere}
.param-editor-controls{justify-content:flex-end}
.param-editor-controls input{width:8ch}
ul{padding:0;list-style:none;margin:0}
img,video,input[type=range]{display:block;width:100%;margin:8px 0}
#roadImage,#replayVideo{max-height:65vh;object-fit:contain;background:#080c10;border-radius:8px}
#replay{scroll-margin-top:12px}
.log a{display:inline-block;margin:6px 12px 0 0}
fieldset{min-inline-size:0}
input[data-recording]{accent-color:var(--accent);min-height:0;width:20px;height:20px;vertical-align:middle;margin:0 7px 0 0}
.record-option{display:flex;align-items:center;min-height:42px}
@media (min-width:840px), (min-width:600px) and (orientation:landscape){
  body{display:grid;grid-template-columns:82px minmax(0,1fr);gap:14px;max-width:1320px}
  main{grid-column:2;grid-row:1;padding-bottom:0}
  nav{grid-column:1;grid-row:1;position:sticky;top:10px;align-self:start;flex-direction:column;justify-content:flex-start;border:1px solid var(--border);border-radius:10px}
  nav button{flex:none;width:100%}
  .settings-layout{grid-template-columns:minmax(115px,175px) minmax(0,1fr);gap:14px}
  #paramGroups{align-self:start}
}
@media (max-width:839px) and (orientation:portrait), (max-width:599px){
  .settings-layout{grid-template-columns:minmax(0,1fr)}
  #paramGroups{flex-direction:row;overflow-x:auto;padding:3px 3px 6px}
  #paramGroups button{flex:none;white-space:nowrap}
}
</style>
<main><header class="page-header"><h1>EON 웹</h1><button id="openWebSettings" aria-haspopup="dialog">웹 설정</button></header>
<section id="drive"><h2>주행</h2>
<div class="card"><h3>전방 화면</h3><img id="roadImage" alt="실시간 전방 카메라" hidden>
<button id="vision">주행 비전 시작</button><p id="visionStatus" aria-live="polite"></p></div>
<div class="card"><h3>장치 상태</h3><pre id="driveStatus"></pre><button id="driveRefresh">상태 새로고침</button></div>
<fieldset class="card"><legend>주행 기록</legend>
<label class="record-option"><input type="checkbox" data-recording="routeLogging" disabled> 주행 로거 (rlog · qlog)</label>
<label class="record-option"><input type="checkbox" data-recording="webVideo" disabled> 리플레이용 전방 영상 녹화</label>
<p>주행 중에도 기록을 시작하거나 종료할 수 있습니다. 영상 옵션 변경 시 현재 파일을 닫고 새 구간을 시작합니다.</p>
<p id="recordingHint"></p><p id="recordingStatus" role="status"></p>
</fieldset><p class="hint">저장된 주행 기록은 로그 탭에서 확인할 수 있습니다.</p></section>
<section id="settings" hidden><h2>설정</h2>
<div class="settings-bar"><input id="filter" type="search" placeholder="설정 이름·설명 검색" aria-label="설정 이름·설명 검색">
<a href="/api/params.json" download="g_web-params.json">JSON 백업</a></div>
<p class="hint">‘인게이지 중 적용’ 표시가 있는 HUD 표시 항목은 인게이지 상태와 관계없이 저장됩니다. 다른 설정은 정차하고 인게이지를 해제한 뒤 저장하세요.</p>
<p id="paramSaveStatus" class="hint" role="status" hidden></p>
<div class="settings-layout"><div id="paramGroups" role="group" aria-label="설정 카테고리"></div><div id="params"></div></div></section>
<section id="tools" hidden><h2>도구</h2>
<div class="card"><h3>Git Commands</h3><p id="gitInfo"></p>
<div class="actions"><button id="gitPull">git pull</button><button id="gitLog">git log</button></div>
<p class="actions"><label>전환할 브랜치 <select id="gitBranch" disabled></select></label>
<button id="gitSwitch" disabled>브랜치 변경</button></p>
<pre id="gitOutput" aria-live="polite"></pre></div>
<div class="card"><h3>장치 관리</h3><div class="actions"><button data-tmux>capture tmux</button>
<button id="eonReboot">재부팅</button><button id="eonRebuild">재부팅하여 전체 재빌드</button></div>
<p id="systemOutput" role="status"></p></div></section>
<section id="records" hidden><h2>로그 · 대시캠</h2><button id="refresh">새로고침</button><div id="replay" class="card" hidden><h3 id="replayTitle"></h3><p id="replayStatus"></p>
<video id="replayVideo" controls playsinline hidden></video>
<button id="replayPlay">재생</button><input id="replaySeek" aria-label="재생 위치" type="range" min="0" step="0.1" value="0">
<pre id="replayValues"></pre></div><p id="logActionStatus" role="status"></p><div id="logs"></div></section>
<section id="terminal" hidden><h2>터미널 로그</h2><button data-tmux>capture tmux</button>
<pre id="tmuxOutput" aria-live="polite"></pre></section>
</main>
<nav aria-label="메뉴">
<button data-page="drive" aria-selected="true">주행</button><button data-page="settings">설정</button>
<button data-page="tools">도구</button><button data-page="records">로그</button><button data-page="terminal">터미널</button>
</nav>
<dialog id="webSettings" aria-labelledby="webSettingsTitle">
<form id="webSettingsForm" method="dialog">
<h2 id="webSettingsTitle">웹 설정</h2>
<div class="card"><label for="startPage">시작 메뉴</label>
<p class="hint">접속할 때 처음 열 메뉴를 이 브라우저에 저장합니다.</p>
<select id="startPage"></select></div>
<div class="dialog-actions"><button id="cancelWebSettings" type="button">취소</button><button type="submit">확인</button></div>
</form></dialog>
<script>
const paramsList = document.querySelector('#params');
const logsList = document.querySelector('#logs');
const filter = document.querySelector('#filter');
const paramSaveStatus = document.querySelector('#paramSaveStatus');
let params = {};
let editable = {};
let catalog = {};
let selectedParamGroup = '';
const paramGroups = document.querySelector('#paramGroups');
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
  paramGroups.replaceChildren();
  const query = filter.value.toLowerCase().trim();
  const entries = Object.entries(params).filter(([key]) => {
    const entry = catalog[key];
    return entry && entry.group !== '기본' && entry.group !== '화면';
  });
  const categories = [...new Set(entries.map(([key]) => catalog[key].group))];
  if (!categories.includes(selectedParamGroup)) selectedParamGroup = categories[0] || '';
  for (const category of categories) {
    const button = document.createElement('button');
    button.textContent = category;
    button.setAttribute('aria-pressed', String(!query && category === selectedParamGroup));
    button.addEventListener('click', () => {
      selectedParamGroup = category;
      filter.value = '';
      renderParams();
      [...paramGroups.children].find(item => item.textContent === category)?.focus();
    });
    paramGroups.append(button);
  }
  const groups = new Map();
  for (const [key, value] of entries) {
    const entry = catalog[key];
    if (query ? ![key, entry.title, entry.description, entry.group].join(' ').toLowerCase().includes(query)
              : entry.group !== selectedParamGroup) continue;
    if (!groups.has(entry.group)) {
      const title = document.createElement('h3');
      title.textContent = entry.group;
      const list = document.createElement('ul');
      paramsList.append(title, list);
      groups.set(entry.group, list);
    }
    const item = document.createElement('li');
    item.className = 'param-row';
    const head = document.createElement('div');
    head.className = 'param-head';
    const title = document.createElement('span');
    title.textContent = entry.title;
    const currentValue = document.createElement('strong');
    currentValue.className = 'param-value';
    currentValue.textContent = value ?? '미설정';
    head.append(title, currentValue);
    const description = document.createElement('p');
    description.className = 'param-description';
    description.textContent = entry.description;
    description.hidden = !entry.description;
    const info = editable[key];
    const meta = document.createElement('p');
    meta.className = 'param-meta';
    meta.textContent = info
      ? key + ' · 범위 ' + info.min + ' ~ ' + info.max + (info.default == null ? '' : ' · 기본값 ' + info.default)
      : key;
    if (info?.liveDisplay) meta.textContent += ' · 인게이지 중 적용';
    item.append(head, meta, description);
    groups.get(entry.group).append(item);
    if (!info) continue;
    const edit = document.createElement('button');
    edit.textContent = '변경';
    edit.setAttribute('aria-expanded', 'false');
    const form = document.createElement('form');
    form.className = 'param-editor';
    form.hidden = true;
    const controls = document.createElement('div');
    controls.className = 'param-editor-controls';
    const input = document.createElement('input');
    input.type = 'number';
    input.min = info.min;
    input.max = info.max;
    input.step = 1;
    input.required = true;
    input.setAttribute('aria-label', entry.title + ' 새 값');
    const step = Number.isSafeInteger(info.unit) && info.unit > 0 ? info.unit : 1;
    const minus = document.createElement('button');
    const plus = document.createElement('button');
    minus.type = plus.type = 'button';
    minus.textContent = '−';
    plus.textContent = '+';
    minus.setAttribute('aria-label', entry.title + ' ' + step + ' 감소');
    plus.setAttribute('aria-label', entry.title + ' ' + step + ' 증가');
    function updateStepButtons() {
      minus.disabled = input.disabled || input.valueAsNumber === info.min;
      plus.disabled = input.disabled || input.valueAsNumber === info.max;
    }
    function changeBy(delta) {
      const current = input.valueAsNumber;
      const base = Number.isSafeInteger(current) ? current : (info.default ?? info.min);
      input.value = Math.min(info.max, Math.max(info.min, base + delta));
      updateStepButtons();
    }
    minus.addEventListener('click', () => changeBy(-step));
    plus.addEventListener('click', () => changeBy(step));
    input.addEventListener('input', updateStepButtons);
    const save = document.createElement('button');
    save.type = 'submit';
    save.textContent = '저장';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.textContent = '취소';
    const status = document.createElement('p');
    status.setAttribute('role', 'status');
    status.hidden = true;
    controls.append(minus, input, plus, save, cancel);
    form.append(controls, status);
    function closeEditor() {
      form.hidden = true;
      edit.setAttribute('aria-expanded', 'false');
      edit.focus();
    }
    edit.addEventListener('click', () => {
      if (!form.hidden) return closeEditor();
      input.value = value ?? '';
      updateStepButtons();
      status.hidden = true;
      form.hidden = false;
      edit.setAttribute('aria-expanded', 'true');
    });
    cancel.addEventListener('click', closeEditor);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const next = input.valueAsNumber;
      if (!Number.isSafeInteger(next) || next < info.min || next > info.max || save.disabled) return;
      edit.disabled = input.disabled = save.disabled = cancel.disabled = true;
      updateStepButtons();
      status.textContent = '저장 중…';
      status.hidden = false;
      try {
        const response = await fetch('/api/params', {method:'POST',
          headers:{'Content-Type':'application/json','X-EON-Action':'set-param'},
          body:JSON.stringify({key, value:next})});
        const result = await response.json();
        if (!response.ok) throw Error(result.output);
        params[key] = String(next);
        paramSaveStatus.textContent = entry.title + ' · ' + next + ' · ' + result.output;
        paramSaveStatus.hidden = false;
        renderParams();
      } catch (error) { status.textContent = error.message; }
      finally {
        edit.disabled = input.disabled = save.disabled = cancel.disabled = false;
        updateStepButtons();
      }
    });
    head.append(edit);
    item.append(form);
  }
  if (!groups.size) paramsList.textContent = '일치하는 설정이 없습니다.';
}
async function removeRoute(route, button) {
  const label = route.replace('--', ' ');
  if (!confirm(label + ' 주행의 모든 구간과 영상을 삭제할까요? 삭제 후 복구할 수 없습니다.')) return;
  const status = document.querySelector('#logActionStatus');
  button.disabled = true;
  status.textContent = '삭제 중…';
  try {
    const response = await fetch('/api/logs/delete', {method:'POST',
      headers:{'Content-Type':'application/json', 'X-EON-Action':'delete-route'},
      body:JSON.stringify({route})});
    const result = await response.json();
    status.textContent = result.output;
    if (response.ok && document.querySelector('#replayTitle').textContent.startsWith(route + '--')) {
      replayRun++;
      stopReplay();
      replay.hidden = true;
    }
    await loadLogs();
  } catch (_) { status.textContent = '삭제 결과를 확인하지 못했습니다. 로그 목록을 새로고침하세요.'; }
  finally { button.disabled = false; }
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
        const remove = document.createElement('button');
        remove.textContent = '이 주행 삭제';
        remove.addEventListener('click', () => removeRoute(route, remove));
        group.open = routes.size === 0;
        group.append(summary, remove);
        logsList.append(group);
        routes.set(route, group);
      }
      const item = document.createElement('div');
      item.textContent = '구간 ' + segment.name.slice(split + 2);
      item.className = 'log';
      routes.get(route).append(item);
      const play = document.createElement('button');
      play.textContent = '리플레이';
      play.addEventListener('click', () => openReplay(segment));
      item.append(' ', play);
      for (const filename of segment.files) {
        const link = document.createElement('a');
        link.href = '/logs/' + segment.name + '/' + filename;
        link.textContent = filename;
        item.append(link);
      }
    }
  } catch (_) { row(logsList, '로그 목록을 불러오지 못했습니다.'); }
}
const recordingInputs = [...document.querySelectorAll('[data-recording]')];
const recordingHint = document.querySelector('#recordingHint');
const recordingStatus = document.querySelector('#recordingStatus');
let recording = null;
let savingRecording = false;
function renderRecording() {
  for (const input of recordingInputs) {
    input.checked = recording?.[input.dataset.recording] === true;
    input.disabled = !recording || savingRecording;
  }
  recordingHint.textContent = recording && !recording.routeLogging
    ? '영상 녹화는 주행 로거를 켰을 때만 작동합니다.' : '';
}
for (const input of recordingInputs) {
  input.addEventListener('change', async () => {
    if (!recording || savingRecording) return;
    const key = input.dataset.recording;
    const enabled = input.checked;
    savingRecording = true;
    for (const control of recordingInputs) control.disabled = true;
    recordingStatus.textContent = '저장 중…';
    try {
      const response = await fetch('/api/recording', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-EON-Action': 'set-recording'},
        body: JSON.stringify({key, enabled})});
      const result = await response.json();
      if (!response.ok) throw Error(result.output);
      recording = result.recording;
      recordingStatus.textContent = result.output;
    } catch (error) {
      recordingStatus.textContent = error.message;
      try { recording = await read('/api/recording'); }
      catch (_) { recording = null; }
    } finally {
      savingRecording = false;
      renderRecording();
    }
  });
}
async function loadSettings() {
  try { ({params, editable, catalog} = await read('/api/params.json')); renderParams(); }
  catch (_) { paramsList.textContent = '설정을 불러오지 못했습니다.'; }
}
async function loadRecording() {
  if (savingRecording) return;
  try { recording = await read('/api/recording'); }
  catch (_) { recording = null; recordingStatus.textContent = '녹화 설정을 불러오지 못했습니다.'; }
  renderRecording();
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
const gitBranch = document.querySelector('#gitBranch');
const gitSwitch = document.querySelector('#gitSwitch');
let currentBranch = '';
function updateBranchButton() {
  gitSwitch.disabled = !gitBranch.value || gitBranch.value === currentBranch;
}
gitBranch.addEventListener('change', updateBranchButton);
const gitOutput = document.querySelector('#gitOutput');
async function loadGit() {
  try {
    const state = await read('/api/git.json');
    currentBranch = state.branch;
    gitInfo.textContent = state.branch + ' · ' + state.commit;
    gitBranch.replaceChildren();
    for (const name of state.branches) {
      const option = document.createElement('option');
      option.value = option.textContent = name;
      gitBranch.append(option);
    }
    gitBranch.value = currentBranch;
    gitBranch.disabled = !state.branches.length;
    updateBranchButton();
  } catch (_) { gitInfo.textContent = 'Git 정보를 읽지 못했습니다.'; }
}
function loadDrive() {
  loadStatus();
  loadRecording();
}
const loaders = {drive: loadDrive, settings: loadSettings, tools: loadGit, records: loadLogs};
const roadImage = document.querySelector('#roadImage');
const visionButton = document.querySelector('#vision');
const visionStatus = document.querySelector('#visionStatus');
let visionRun = 0;
let visionActive = false;
let visionUrl = '';
function stopVision() {
  visionRun++;
  visionActive = false;
  visionButton.textContent = '주행 비전 시작';
  roadImage.hidden = true;
  roadImage.removeAttribute('src');
  if (visionUrl) URL.revokeObjectURL(visionUrl);
  visionUrl = '';
  visionStatus.textContent = '';
}
visionButton.addEventListener('click', async () => {
  if (visionActive) return stopVision();
  const run = ++visionRun;
  visionActive = true;
  visionButton.textContent = '중지';
  while (run === visionRun) {
    const started = performance.now();
    try {
      const response = await fetch('/api/road.jpg', {cache:'no-store'});
      if (!response.ok) throw Error();
      const blob = await response.blob();
      if (run !== visionRun) return;
      if (visionUrl) URL.revokeObjectURL(visionUrl);
      visionUrl = URL.createObjectURL(blob);
      roadImage.src = visionUrl;
      roadImage.hidden = false;
      visionStatus.textContent = '';
    } catch (_) {
      if (run === visionRun) {
        roadImage.hidden = true;
        visionStatus.textContent = '전방 카메라 연결 대기 중';
      }
    }
    await new Promise(resolve => setTimeout(resolve, Math.max(0, 340 - (performance.now() - started))));
  }
});
document.addEventListener('visibilitychange', () => { if (document.hidden) stopVision(); });

const replay = document.querySelector('#replay');
const video = document.querySelector('#replayVideo');
const seek = document.querySelector('#replaySeek');
const play = document.querySelector('#replayPlay');
const values = document.querySelector('#replayValues');
const replayStatus = document.querySelector('#replayStatus');
let points = [];
let offset = 0;
let timer = null;
let replayRun = 0;
function stopReplay() {
  clearInterval(timer);
  timer = null;
  video.pause();
  play.textContent = '재생';
}
function replayAt(t) {
  seek.value = t;
  const latest = {};
  for (let i = points.length - 1; i >= 0; i--) {
    const [stamp, data] = points[i];
    if (stamp > t + 0.05) continue;
    if (stamp < t - 0.5) break;
    for (const [key, value] of Object.entries(data)) if (!(key in latest)) latest[key] = value;
  }
  const show = (key, unit) => latest[key] == null ? '—' : latest[key] + ' ' + unit;
  values.textContent = [
    t.toFixed(1) + '초', '차량 속도 ' + show('speed', 'km/h'),
    '앞차 속도 ' + show('leadSpeed', 'km/h'), '차간 거리 ' + show('distance', 'm'),
    '요청 가속도 ' + show('target', 'm/s²'), '실제 가속도 ' + show('accel', 'm/s²')
  ].join('\n');
}
async function openReplay(segment) {
  const run = ++replayRun;
  stopReplay();
  points = [];
  replay.hidden = false;
  video.hidden = true;
  video.removeAttribute('src');
  video.load();
  values.textContent = '';
  replayStatus.textContent = '로그 읽는 중…';
  document.querySelector('#replayTitle').textContent = segment.name;
  replay.scrollIntoView();
  try {
    const data = await read('/api/replay/' + segment.name);
    if (run !== replayRun) return;
    points = data.points;
    offset = data.videoOffset ?? 0;
    seek.max = points[points.length - 1][0];
    if (segment.files.includes('qcamera.mp4')) {
      video.src = '/video/' + segment.name;
      video.hidden = false;
    }
    replayStatus.textContent = video.hidden ? '주행 데이터 재생 · 저장 영상 없음' :
      (data.videoOffset === null ? '영상 시작 시각 기록 없음 · 구간 시작 기준' : '영상과 주행 데이터');
    replayAt(0);
  } catch (_) { if (run === replayRun) replayStatus.textContent = '이 구간을 재생할 수 없습니다.'; }
}
seek.addEventListener('input', () => {
  const t = Number(seek.value);
  replayAt(t);
  if (!video.hidden && video.readyState) video.currentTime = Math.max(0, t - offset);
});
video.addEventListener('timeupdate', () => {
  if (!video.hidden) replayAt(Math.max(0, video.currentTime + offset));
});
video.addEventListener('error', () => {
  video.hidden = true;
  replayStatus.textContent = '영상 재생 실패 · 주행 데이터만 재생 가능';
});
video.addEventListener('play', () => { play.textContent = '일시정지'; });
video.addEventListener('pause', () => { play.textContent = '재생'; });
play.addEventListener('click', () => {
  if (!points.length) return;
  if (!video.hidden) {
    if (video.paused) video.play().catch(() => { video.hidden = true; play.click(); });
    else video.pause();
    return;
  }
  if (timer !== null) return stopReplay();
  if (seek.value === seek.max) replayAt(0);
  const start = performance.now() - Number(seek.value) * 1000;
  play.textContent = '일시정지';
  timer = setInterval(() => {
    const t = Math.min(Number(seek.max), (performance.now() - start) / 1000);
    replayAt(t);
    if (t >= Number(seek.max)) stopReplay();
  }, 100);
});
function openPage(id) {
  if (id !== 'drive') stopVision();
  if (id !== 'records') { replayRun++; stopReplay(); }
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
  if (!confirm('openpilot 인게이지를 해제한 상태에서 g_web 업데이트를 가져올까요?')) return;
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
gitSwitch.addEventListener('click', async () => {
  const branch = gitBranch.value;
  if (!branch || branch === currentBranch) return;
  if (!confirm(branch + ' 브랜치로 전환할까요? 재부팅 후 대상 브랜치에 EON 웹이 없으면 SSH로 접속해야 합니다.')) return;
  gitBranch.disabled = gitSwitch.disabled = true;
  gitOutput.textContent = '브랜치 전환 중…';
  try {
    const response = await fetch('/api/git/switch', {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-EON-Action': 'git-switch'},
      body: JSON.stringify({branch})});
    gitOutput.textContent = (await response.json()).output;
  } catch (_) { gitOutput.textContent = '응답을 읽지 못했습니다. 현재 브랜치를 확인하세요.'; }
  finally { await loadGit(); }
});
const systemOutput = document.querySelector('#systemOutput');
for (const [id, action, message] of [
  ['eonReboot', 'reboot', '정차하고 openpilot 인게이지를 해제한 상태에서 EON을 재부팅할까요?'],
  ['eonRebuild', 'rebuild', '재부팅하여 전체 재빌드를 시작할까요? 빌드 중에는 EON 웹 연결이 끊깁니다.']
]) {
  const button = document.getElementById(id);
  button.addEventListener('click', async () => {
    if (!confirm(message)) return;
    button.disabled = true;
    systemOutput.textContent = '요청 중…';
    try {
      const response = await fetch('/api/system/' + action, {method: 'POST',
        headers: {'X-EON-Action': action}});
      systemOutput.textContent = (await response.json()).output;
      if (!response.ok) button.disabled = false;
    } catch (_) {
      systemOutput.textContent = 'EON 응답을 받지 못했습니다. 장치 화면에서 상태를 확인하세요.';
      button.disabled = false;
    }
  });
}
filter.addEventListener('input', renderParams);
document.querySelector('#driveRefresh').addEventListener('click', loadDrive);
document.querySelector('#refresh').addEventListener('click', loadLogs);
const webSettings = document.querySelector('#webSettings');
const startPage = document.querySelector('#startPage');
const startPageKey = 'eon-web-start-page';
for (const button of document.querySelectorAll('[data-page]')) {
  const option = document.createElement('option');
  option.value = button.dataset.page;
  option.textContent = button.textContent;
  startPage.append(option);
}
let initialPage = 'drive';
const stored = document.cookie.split('; ').find(cookie => cookie.startsWith(startPageKey + '='));
const savedPage = stored?.slice(startPageKey.length + 1);
if ([...startPage.options].some(option => option.value === savedPage)) initialPage = savedPage;
document.querySelector('#openWebSettings').addEventListener('click', () => {
  startPage.value = initialPage;
  webSettings.showModal();
});
document.querySelector('#cancelWebSettings').addEventListener('click', () => webSettings.close());
document.querySelector('#webSettingsForm').addEventListener('submit', () => {
  initialPage = startPage.value;
  document.cookie = startPageKey + '=' + initialPage + '; Path=/; Max-Age=31536000; SameSite=Strict';
});
openPage(initialPage);
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


def settings_catalog():
  with open(os.path.join(BASEDIR, "selfdrive", "apilot.json"), encoding="utf-8") as source:
    return json.load(source)["params"]


def settings_snapshot():
  params = Params()
  values = {}
  catalog = {}
  rows = settings_catalog()
  for entry in rows:
    key = entry["name"]
    try:
      values[key] = params.get(key, encoding="utf-8")
    except (UnknownKeyName, UnicodeDecodeError):
      continue
    catalog[key] = {"title": entry.get("title") or key, "group": entry.get("group") or "기타",
                    "description": entry.get("descr") or ""}
  editable = {key: dict(rule, liveDisplay=key in LIVE_HUD_DISPLAY_PARAMS)
              for key, rule in param_rules(rows).items() if key in values}
  return {"branch": "g_web", "params": values, "catalog": catalog, "editable": editable}


def param_rules(rows=None):
  if rows is None:
    rows = settings_catalog()
  return {row["name"]: {key: row[key] for key in ("title", "min", "max", "unit", "default")}
          for row in rows if type(row.get("min")) is int and type(row.get("max")) is int
          and row["min"] <= row["max"]}


def recording_settings():
  return {key: path.is_file() for key, path in RECORDING_FLAGS.items()}


def set_recording(key, enabled):
  path = RECORDING_FLAGS.get(key) if isinstance(key, str) else None
  if path is None or type(enabled) is not bool:
    return 400, {"output": "잘못된 녹화 설정입니다."}
  if path.is_file() != enabled:
    if enabled:
      path.touch()
    else:
      try:
        path.unlink()
      except FileNotFoundError:
        pass
  return 200, {"output": "저장했습니다. 주행 중에는 파일 마무리 후 반영됩니다.", "recording": recording_settings()}


# These settings are read by the HUD display packet builder, outside vehicle control.
LIVE_HUD_DISPLAY_PARAMS = frozenset((
  "EonClusterHudBrightness", "EonClusterHudDayBrightness", "EonClusterHudNightBrightness",
  "EonClusterHudInfoBox", "EonClusterHudTheme", "EonClusterHudLanguage",
  "EonClusterHudGuardrail", "EonClusterHudHaze",
))


def set_param(key, value):
  rule = param_rules().get(key) if isinstance(key, str) else None
  if rule is None or type(value) is not int or not rule["min"] <= value <= rule["max"]:
    return 400, "허용된 설정과 값 범위를 확인하세요."
  if key not in LIVE_HUD_DISPLAY_PARAMS:
    reason = maintenance_block_reason(require_standstill=True)
    if reason:
      return 409, reason
  try:
    Params().put(key, str(value))
  except UnknownKeyName:
    return 400, "이 기기에 등록되지 않은 설정입니다."
  return 200, "저장했습니다."


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
             if not (directory / (filename + ".lock")).exists()
             and not (directory / filename).is_symlink() and (directory / filename).is_file()]
    if files:
      result.append({"name": name, "files": files})
    if len(result) == 120:
      break
  return result


def delete_route(route):
  if not isinstance(route, str) or not SEGMENT.fullmatch(route + "--0"):
    return 400, "삭제할 주행을 목록에서 선택하세요."
  state = read_device_state(check_ignition=True, allow_no_panda=True)
  if state["started"] or not state["ignitionOff"] or not Params().get_bool("IsOffroad"):
    return 409, "시동을 끈 오프로드 상태에서 삭제하세요."
  try:
    with os.scandir(ROOT) as entries:
      paths = [entry.path for entry in entries if SEGMENT.fullmatch(entry.name)
               and entry.name.rsplit("--", 1)[0] == route and entry.is_dir(follow_symlinks=False)]
  except FileNotFoundError:
    paths = []
  finished = []
  for path in paths:
    try:
      if any(name.endswith(".lock") for name in os.listdir(path)):
        return 409, "기록 파일이 아직 잠겨 있어 삭제하지 않았습니다."
    except FileNotFoundError:
      continue
    finished.append(path)

  deleted = 0
  for path in finished:
    try:
      shutil.rmtree(path)
      deleted += 1
    except FileNotFoundError:
      continue
  return 200, "주행 구간 {}개를 삭제했습니다.".format(deleted)


def open_log(segment, filename):
  if not SEGMENT.fullmatch(segment) or filename not in LOG_FILES:
    raise FileNotFoundError
  directory = os.open(os.path.join(ROOT, segment), os.O_DIRECTORY | os.O_NOFOLLOW)
  try:
    locks = os.listdir(directory)
    if "rlog.bz2.lock" in locks or filename + ".lock" in locks:
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


def git_branches():
  status, output = command(["git", "for-each-ref", "--format=%(refname) %(symref)",
                            "refs/heads", "refs/remotes/origin"])
  if status:
    raise RuntimeError("브랜치 목록을 읽지 못했습니다.")
  branches = {}
  for line in output.splitlines():
    ref, _, target = line.partition(" ")
    if not target and ref.startswith("refs/remotes/origin/"):
      name = ref[len("refs/remotes/origin/"):]
      if not name.startswith("-") and name != "HEAD":
        branches.setdefault(name, ref)
    elif not target and ref.startswith("refs/heads/"):
      name = ref[len("refs/heads/"):]
      if not name.startswith("-"):
        branches[name] = ref
  return branches


def git_info():
  branch_status, branch = command(["git", "symbolic-ref", "--quiet", "--short", "HEAD"])
  commit_status, commit = command(["git", "rev-parse", "--short", "HEAD"])
  history_status, history = command(["git", "log", "-10", "--oneline", "--no-color"])
  if branch_status or commit_status or history_status:
    raise RuntimeError("Git 정보를 읽지 못했습니다.")
  return {"branch": branch, "commit": commit, "history": history, "branches": sorted(git_branches())}


def panda_usb_present():
  import usb1

  try:
    with usb1.USBContext() as context:
      return any(device.getVendorID() == 0xbbaa for device in context.getDeviceList(skip_on_error=False))
  except usb1.USBError as error:
    raise RuntimeError("USB 장치 목록을 읽지 못했습니다.") from error


def read_device_state(check_ignition=False, allow_no_panda=False):
  from cereal import messaging

  services = ["deviceState", "pandaStates"] if check_ignition else ["deviceState"]
  subscriber = messaging.SubMaster(services)
  seen = set()
  usb_checked = False
  deadline = time.monotonic() + 3
  while time.monotonic() < deadline:
    subscriber.update(200)
    for service in services:
      if subscriber.updated[service] and subscriber.valid[service]:
        seen.add(service)
    if "deviceState" not in seen or not subscriber.valid["deviceState"]:
      continue
    device = subscriber["deviceState"]
    temperatures = list(device.cpuTempC)
    state = {"started": device.started, "freeSpace": round(device.freeSpacePercent),
             "cpuTemp": round(max(temperatures)) if temperatures else None}
    if not check_ignition:
      return state
    if "pandaStates" in seen and subscriber.valid["pandaStates"]:
      pandas = list(subscriber["pandaStates"])
      if any(p.ignitionLine or p.ignitionCan for p in pandas):
        return dict(state, ignitionOff=False)
      if pandas and all(str(p.pandaType) != "unknown" for p in pandas):
        return dict(state, ignitionOff=True)
    if allow_no_panda and not usb_checked and not device.started and Params().get_bool("IsOffroad"):
      usb_checked = True
      try:
        if not panda_usb_present():
          return dict(state, ignitionOff=True)
      except (ImportError, OSError, RuntimeError) as error:
        raise RuntimeError("판다 USB 연결 상태를 확인하지 못했습니다.") from error
  raise RuntimeError("EON 또는 판다 상태를 확인하지 못했습니다.")


def maintenance_block_reason(require_standstill=False):
  state = read_device_state(check_ignition=True, allow_no_panda=True)
  if not state["started"] and state["ignitionOff"] and Params().get_bool("IsOffroad"):
    return None

  from cereal import messaging

  services = ["controlsState", "carState"] if require_standstill else ["controlsState"]
  subscriber = messaging.SubMaster(services, poll=["controlsState"])
  deadline = time.monotonic() + 1
  while time.monotonic() < deadline:
    subscriber.update(100)
    if subscriber.updated["controlsState"] and subscriber.valid["controlsState"]:
      now = messaging.sec_since_boot()
      if 0 <= now - subscriber.logMonoTime["controlsState"] / 1e9 < 0.5:
        controls = subscriber["controlsState"]
        if controls.enabled or controls.active:
          return "openpilot 인게이지를 해제한 뒤 다시 시도하세요."
        if require_standstill:
          if not subscriber.valid["carState"] or not 0 <= now - subscriber.logMonoTime["carState"] / 1e9 < 0.5:
            continue
          car = subscriber["carState"]
          if not car.standstill or not abs(car.vEgoRaw) < 0.1:
            return "차량을 완전히 정차한 뒤 다시 시도하세요."
        return None
  return ("인게이지 또는 차량 정차 상태를 확인할 수 없습니다." if require_standstill
          else "openpilot 인게이지 상태를 확인할 수 없습니다.")


def git_pull():
  if git_info()["branch"] != "g_web":
    return False, "g_web 브랜치에서만 업데이트할 수 있습니다."
  status, changes = command(["git", "status", "--porcelain", "--untracked-files=no"])
  if status or changes:
    return False, "로컬 추적 파일에 변경이 있습니다. SSH에서 git status를 확인하세요."
  reason = maintenance_block_reason()
  if reason:
    return False, reason
  status, output = command(["git", "fetch", "--no-recurse-submodules", "origin", "g_web"], timeout=35)
  if status:
    return False, output or "원격 브랜치를 가져오지 못했습니다."
  status, target = command(["git", "rev-parse", "--verify", "FETCH_HEAD"])
  if status:
    return False, target or "가져온 커밋을 확인하지 못했습니다."
  before = git_info()["commit"]
  reason = maintenance_block_reason()
  if reason:
    return False, reason
  status, output = command(["git", "-c", "core.hooksPath=/dev/null", "merge", "--ff-only", target])
  if status:
    return False, output or "Git Pull에 실패했습니다."
  after = git_info()["commit"]
  detail = "다음 EON 재부팅부터 새 코드가 적용됩니다." if before != after else "이미 최신 커밋입니다."
  return True, "{} → {}\n{}\n{}".format(before, after, detail, output)


def git_switch(branch):
  ref = git_branches().get(branch) if isinstance(branch, str) else None
  if ref is None:
    return 400, "목록에서 전환할 브랜치를 선택하세요."
  status, changes = command(["git", "status", "--porcelain", "--untracked-files=no"])
  if status or changes:
    return 409, "로컬 추적 파일에 변경이 있습니다.\n" + changes
  state = read_device_state(check_ignition=True, allow_no_panda=True)
  if state["started"] or not state["ignitionOff"]:
    return 409, "시동을 끈 뒤 다시 시도하세요."
  args = ["git", "-c", "core.hooksPath=/dev/null", "checkout", "--no-overwrite-ignore"]
  args += ["-b", branch, "--track", ref] if ref.startswith("refs/remotes/") else [branch]
  status, output = command(args, timeout=25)
  if status:
    return 409, output or "브랜치 전환에 실패했습니다."
  return 200, branch + " 전환 완료. 재부팅하면 적용됩니다.\n" + output


def request_reboot(rebuild=False):
  if rebuild and git_info()["branch"] != "g_web":
    return 409, "전체 재빌드는 g_web 브랜치에서 실행하세요."
  params = Params()
  if rebuild:
    state = read_device_state(check_ignition=True, allow_no_panda=True)
    if state["started"] or not state["ignitionOff"] or not params.get_bool("IsOffroad"):
      return 409, "시동을 끈 오프로드 상태에서 다시 시도하세요."
    REBUILD_FLAG.touch()
  else:
    reason = maintenance_block_reason(require_standstill=True)
    if reason:
      return 409, reason
  params.put_bool("DoReboot", True)
  return 200, "재부팅을 요청했습니다. 전체 재빌드는 부팅 중 진행됩니다." if rebuild else "재부팅을 요청했습니다."


class WebServer(HTTPServer):
  def __init__(self, *args):
    self.road_preview = RoadPreview()
    super().__init__(*args)

  def service_actions(self):
    self.road_preview.expire()


class Handler(BaseHTTPRequestHandler):
  timeout = 5

  def log_message(self, *_args):
    pass

  def begin(self, status, mime, size, attachment=None, extra_headers=()):
    self.send_response(status)
    self.send_header("Content-Type", mime)
    self.send_header("Content-Length", str(size))
    self.send_header("Cache-Control", "no-store")
    self.send_header("X-Content-Type-Options", "nosniff")
    self.send_header("X-Frame-Options", "DENY")
    for name, value in extra_headers:
      self.send_header(name, value)
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
    action = {"/api/git/pull": "git-pull", "/api/params": "set-param",
              "/api/recording": "set-recording", "/api/git/switch": "git-switch",
              "/api/logs/delete": "delete-route",
              "/api/system/reboot": "reboot", "/api/system/rebuild": "rebuild"}.get(self.path)
    if action is None:
      self.send_error(404)
      return
    # LAN HTTP browsers can omit Fetch Metadata, but send Origin for these POSTs.
    host = self.headers.get("Host")
    origin = self.headers.get("Origin")
    fetch_site = self.headers.get("Sec-Fetch-Site")
    if (not host or origin != "http://" + host or fetch_site not in (None, "same-origin")
        or self.headers.get("X-EON-Action") != action):
      self.send_json({"output": "EON 웹 화면에서 실행하세요."}, 403)
      return
    try:
      if action in ("set-param", "set-recording", "git-switch", "delete-route"):
        length = self.headers.get("Content-Length", "")
        if not length.isdigit() or len(length) > 4 or not 0 < int(length) <= 1024:
          self.send_json({"output": "잘못된 요청입니다."}, 400)
          return
        try:
          payload = json.loads(self.rfile.read(int(length)))
        except (ValueError, UnicodeError):
          payload = None
        if not isinstance(payload, dict):
          self.send_json({"output": "잘못된 요청입니다."}, 400)
          return
        if action == "set-recording":
          status, result = set_recording(payload.get("key"), payload.get("enabled"))
        elif action == "delete-route":
          status, output = delete_route(payload.get("route"))
          result = {"output": output}
        elif action == "git-switch":
          status, output = git_switch(payload.get("branch"))
          result = {"output": output}
        else:
          status, output = set_param(payload.get("key"), payload.get("value"))
          result = {"output": output}
        self.send_json(result, status)
        return
      if action in ("reboot", "rebuild"):
        status, output = request_reboot(rebuild=action == "rebuild")
        self.send_json({"output": output}, status)
        return
      ok, output = git_pull()
      self.send_json({"output": output}, 200 if ok else 409)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
      self.send_json({"output": str(error)}, 503)

  def do_GET(self):
    path = urlsplit(self.path).path
    if path == "/eon-web.webmanifest":
      self.send_data(json.dumps(WEB_MANIFEST).encode("utf-8"), "application/manifest+json")
      return
    icon = ICON_FILES.get(path)
    if icon:
      try:
        data = (Path(BASEDIR) / "selfdrive" / "assets" / icon).read_bytes()
      except OSError:
        self.send_error(404)
        return
      self.send_data(data, "image/png")
      return
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
    elif path == "/api/recording":
      self.send_json(recording_settings())
    elif path == "/api/params.json":
      self.send_json(settings_snapshot())
    elif path == "/api/logs.json":
      self.send_json(segments())
    elif path == "/api/road.jpg":
      try:
        frame = self.server.road_preview.frame()
      except (ImportError, OSError, ValueError):
        frame = None
      if frame is None:
        self.send_json({"output": "전방 카메라 준비 중"}, 503)
      else:
        self.send_data(frame, "image/jpeg")
    elif path.startswith("/api/replay/"):
      segment = path.rsplit("/", 1)[-1]
      if not SEGMENT.fullmatch(segment):
        self.send_error(404)
        return
      try:
        self.send_json(replay_data(segment, open_log))
      except (ImportError, RuntimeError, ValueError) as error:
        self.send_json({"output": str(error)}, 503)
    elif path.startswith("/video/"):
      self.serve_video(path.rsplit("/", 1)[-1])
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


  def serve_video(self, segment):
    try:
      source = open_log(segment, VIDEO_FILE)
    except OSError:
      self.send_error(404)
      return
    with source:
      size = os.fstat(source.fileno()).st_size
      header = self.headers.get("Range")
      headers = [("Accept-Ranges", "bytes")]
      try:
        start, end = media_range(header, size)
      except ValueError:
        headers.append(("Content-Range", "bytes */{}".format(size)))
        self.begin(416, "video/mp4", 0, extra_headers=headers)
        return
      if header:
        headers.append(("Content-Range", "bytes {}-{}/{}".format(start, end - 1, size)))
      self.begin(206 if header else 200, "video/mp4", end - start, extra_headers=headers)
      source.seek(start)
      remaining = end - start
      while remaining:
        chunk = source.read(min(64 * 1024, remaining))
        if not chunk:
          break
        self.wfile.write(chunk)
        remaining -= len(chunk)


def main():
  value = password()
  if not value:
    raise RuntimeError("EON web password file is empty")
  with WebServer(("0.0.0.0", PORT), Handler) as server:
    server.authorization = b"Basic " + base64.b64encode(("eon:" + value).encode("utf-8"))
    server.serve_forever()


if __name__ == "__main__":
  main()
