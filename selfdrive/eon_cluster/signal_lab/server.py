#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""EON camera/result bridge. Standalone observer, never publishes vehicle controls."""
import argparse
import collections
import hmac
import io
import json
import math
import os
from pathlib import Path
import secrets
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL_SHA = '458c8f1273ae407e6fa8334159a7f6dc29c88a2680b15eae2771105b0bdeffc2'
LABELS = ('green', 'left', 'red', 'yellow')
MAX_RESULT_AGE = 0.85
DISPLAY_TTL = 1.2
MAX_BODY = 32768



PAIRING_FILE = '/data/eon_signal_lab/token'


def valid_code(value):
  return isinstance(value, str) and re.fullmatch(r'[0-9]{4}', value) is not None


def load_pairing_code(path=PAIRING_FILE):
  """Persist a four-digit code, migrating an old 32-character token atomically."""
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  try:
    code = path.read_text().strip()
  except FileNotFoundError:
    code = ''
  if not valid_code(code):
    code = '%04d' % secrets.randbelow(10000)
    tmp = path.with_name(path.name + '.%d.tmp' % os.getpid())
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
      f.write(code)
    os.replace(str(tmp), str(path))
  os.chmod(str(path), 0o600)
  return code


def pairing_info(path=PAIRING_FILE):
  """Only exposed through the authenticated EON Web UI."""
  try:
    code = Path(path).read_text().strip()
    return {'code': code if valid_code(code) else None, 'port': 7799,
            'observer_only': True}
  except OSError:
    return {'code': None, 'port': 7799, 'observer_only': True}


class PairingAuth:
  """Bound failed four-digit attempts per source IP; successful sessions reset it."""
  def __init__(self, code, clock=time.monotonic):
    if not valid_code(code):
      raise ValueError('pairing code must be four ASCII digits')
    self.expected = ('Bearer ' + code).encode('ascii')
    self.clock = clock
    self.lock = threading.Lock()
    self.failures = collections.OrderedDict()

  def status(self, ip, supplied):
    now = self.clock()
    with self.lock:
      count, since, until = self.failures.get(ip, (0, now, 0))
      if now < until:
        return 429
      if hmac.compare_digest(supplied.encode('utf-8'), self.expected):
        self.failures.pop(ip, None)
        return 200
      if now - since >= 60:
        count, since = 0, now
      count += 1
      until = now + 30 if count >= 5 else 0
      self.failures[ip] = (count, since, until)
      self.failures.move_to_end(ip)
      while len(self.failures) > 256:
        self.failures.popitem(last=False)
      return 429 if until else 401


class BadResult(ValueError):
  pass


class CandidateFilter:
  """Conservative display heuristic; central position does not establish ego relevance.

  Coordinates refer to the receiver's upper-70% camera crop. The lower band is
  excluded to reduce vehicle-lamp candidates, at the cost of missing low heads.
  Never carry a colour across missing, conflicting, or spatially changed evidence.
  """
  VERSION = 1
  CONFIRM_SECONDS = .6
  MAX_GAP = .65

  def __init__(self):
    self.reset()

  def reset(self):
    self.previous = None
    self.signature = None
    self.since = self.last = None
    self.hits = 0

  @staticmethod
  def center(d):
    x0, y0, x1, y1 = d['box']
    return (x0 + x1) / 2, (y0 + y1) / 2

  @staticmethod
  def overlap(a, b):
    ax, ay, ar, ab = a['box']
    bx, by, br, bb = b['box']
    inter = max(0, min(ar, br) - max(ax, bx)) * max(0, min(ab, bb) - max(ay, by))
    return inter / max(1e-9, min((ar-ax)*(ab-ay), (br-bx)*(bb-by)))

  @classmethod
  def result(cls, status, detections=None):
    return {'version': cls.VERSION, 'status': status, 'detections': detections or [],
            'ego_signal_confirmed': False}

  def update(self, detections, now):
    groups = []
    for d in sorted(detections, key=lambda d: -d['score']):
      x, y = self.center(d)
      x0, y0, x1, y1 = d['box']
      if d['score'] < .35 or not (.2 <= x <= .8 and .08 <= y <= .72):
        continue
      if x1-x0 > .30 or y1-y0 > .18:
        continue
      group = next((g for g in groups if self.overlap(g[0], d) > .4), None)
      if group is None:
        groups.append([d])
      elif not any(p['label'] == d['label'] for p in group):
        group.append(d)  # Keep simultaneous red + left; suppress duplicate tile boxes.
    if not groups:
      self.reset()
      return self.result('none')
    groups.sort(key=lambda g: abs(self.center(g[0])[0] - .5))
    if len(groups) > 1 and (abs(self.center(groups[1][0])[0] - .5) -
                            abs(self.center(groups[0][0])[0] - .5)) < .04:
      self.reset()
      return self.result('ambiguous')
    chosen = groups[0]
    signature = frozenset(d['label'] for d in chosen)
    if ('red' in signature and 'green' in signature or
        'yellow' in signature and len(signature) > 1):
      self.reset()
      return self.result('conflict')
    representative = chosen[0]
    cx, cy = self.center(representative)
    same = False
    if self.previous is not None and self.signature == signature and self.last < now <= self.last + self.MAX_GAP:
      px, py = self.center(self.previous)
      same = (self.overlap(self.previous, representative) > .1 and
              abs(cx-px) <= .04 and abs(cy-py) <= .04)
    if not same:
      self.since, self.hits = now, 1
    else:
      self.hits += 1
    self.previous, self.signature, self.last = representative, signature, now
    stable = self.hits >= 3 and now - self.since >= self.CONFIRM_SECONDS
    return self.result('stable' if stable else 'warming', chosen if stable else None)


class Results:
  def __init__(self, clock=time.monotonic, output=None):
    self.clock, self.output = clock, output
    self.session = secrets.token_hex(12)
    self.lock = threading.Lock()
    self.frames = collections.OrderedDict()
    self.seq, self.last_accepted = 0, 0
    self.latest, self.received_at = None, 0.0
    self.candidates = CandidateFilter()

  def issue(self):
    with self.lock:
      self.seq += 1
      self.frames[self.seq] = self.clock()
      while len(self.frames) > 16:
        self.frames.popitem(last=False)
      return self.seq

  def accept(self, data):
    if not isinstance(data, dict) or data.get('session') != self.session:
      raise BadResult('session mismatch; fetch a fresh frame')
    if data.get('model_sha256') != MODEL_SHA:
      raise BadResult('model identity mismatch')
    frame = data.get('frame_id')
    if type(frame) is not int:
      raise BadResult('invalid frame id')
    detections = data.get('detections')
    if not isinstance(detections, list) or len(detections) > 32:
      raise BadResult('invalid detections')
    clean = []
    for d in detections:
      if not isinstance(d, dict) or d.get('label') not in LABELS:
        raise BadResult('invalid label')
      score, box = d.get('score'), d.get('box')
      if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
        raise BadResult('invalid confidence')
      if not isinstance(box, list) or len(box) != 4 or any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1 for x in box):
        raise BadResult('invalid box')
      if box[2] <= box[0] or box[3] <= box[1]:
        raise BadResult('empty box')
      clean.append({'label': d['label'], 'score': round(score, 4), 'box': box})
    infer_ms = data.get('infer_ms', 0)
    if type(infer_ms) not in (int, float) or not math.isfinite(infer_ms) or not 0 <= infer_ms <= 60000:
      raise BadResult('invalid inference time')
    with self.lock:
      now = self.clock()
      issued = self.frames.get(frame)
      if issued is None or frame <= self.last_accepted:
        raise BadResult('unknown, repeated or out-of-order frame')
      age = now - issued
      if age < 0 or age > MAX_RESULT_AGE:
        raise BadResult('result too old')
      self.last_accepted = frame
      self.latest = {'protocol': 1, 'session': self.session, 'frame_id': frame,
                     'observer_only': True, 'ego_signal_confirmed': False,
                     'model_sha256': MODEL_SHA, 'model': 'Korean LightSignal v20260727',
                     'detections': clean, 'selection': self.candidates.update(clean, now),
                     'infer_ms': round(infer_ms, 1),
                     'roundtrip_ms': round(age * 1000, 1), 'received_unix_ms': int(time.time() * 1000)}
      self.received_at = now
      if self.output:
        # Separate file: neither navigation_route nor longitudinal control consumes it.
        dest = Path(self.output)
        tmp = dest.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.latest, ensure_ascii=False), encoding='utf-8')
        os.replace(str(tmp), str(dest))
      return dict(self.latest)

  def snapshot(self):
    with self.lock:
      age = self.clock() - self.received_at
      if self.latest is None or age < 0 or age > DISPLAY_TTL:
        return {'protocol': 1, 'observer_only': True, 'ego_signal_confirmed': False,
                'status': 'stale', 'detections': [], 'selection': CandidateFilter.result('none')}
      out = dict(self.latest)
      out['age_ms'] = int(age * 1000)
      out['status'] = 'candidate' if out['detections'] else 'none'
      return out


class Camera:
  def __init__(self):
    self.client = None
    self.sm = None
    self.lock = threading.Lock()
    self.last_at = 0.0

  def frame(self):
    from PIL import Image
    from cereal.visionipc.visionipc_pyx import VisionIpcClient, VisionStreamType
    import cereal.messaging as messaging
    with self.lock:
      if time.monotonic() - self.last_at < .18:
        raise RuntimeError('capture rate limited')
      self.last_at = time.monotonic()
      if self.sm is None:
        self.sm = messaging.SubMaster(['roadCameraState'])
      if self.client is None:
        self.client = VisionIpcClient('camerad', VisionStreamType.VISION_STREAM_RGB_ROAD, True)
        if not self.client.connect(False):
          self.client = None
          raise RuntimeError('EON road camera is not ready')
      data = self.client.recv(100)
      self.sm.update(0)
      # Reject old or absent camera metadata. VisionIpc's Python binding in this
      # branch exposes no frame timestamp; it cannot prove exact frame/message pairing.
      camera_age = time.monotonic() - self.sm.logMonoTime['roadCameraState'] / 1e9
      if data is None or not self.sm.alive['roadCameraState'] or not 0 <= camera_age <= .5:
        self.client = None
        raise RuntimeError('camera frame or timestamp is stale')
      w, h, stride = self.client.width, self.client.height, self.client.stride
      image = Image.frombytes('RGB', (w, h), data.tobytes(), 'raw', 'BGR', stride)
      # Keep original pixels in the upper road region, capped only above 1280 px.
      image = image.crop((0, 0, w, max(1, int(h * .70))))
      if image.width > 1280:
        image = image.resize((1280, int(image.height * 1280 / image.width)), Image.Resampling.BILINEAR if hasattr(Image, 'Resampling') else Image.BILINEAR)
      out = io.BytesIO()
      image.save(out, 'JPEG', quality=85)
      return out.getvalue(), image.width, image.height


PAGE = r'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>EON 한국 신호등 시험</title><style>body{background:#111827;color:white;font:20px sans-serif;padding:24px}input,button{font:inherit;max-width:95%}pre{white-space:pre-wrap}b{color:#fbbf24}</style><h2>한국 신호등 · 관찰 모드</h2><b>표시는 후보입니다. 내 진행 방향의 신호로 확정하지 않습니다.</b><p><input id="token" type="password" inputmode="numeric" maxlength="4" pattern="[0-9]{4}" placeholder="4자리 연결 코드"><button id="go">연결</button></p><pre id="out">앱에서 인식을 시작하세요.</pre><script>let active=false;const out=document.querySelector('#out'),token=document.querySelector('#token');token.value=location.hash.slice(1);document.querySelector('#go').onclick=()=>{active=true;poll()};const names={red:'빨강',yellow:'노랑',green:'초록',left:'좌회전'};async function poll(){if(!active)return;try{const r=await fetch('/state',{headers:{Authorization:'Bearer '+token.value},cache:'no-store'});if(!r.ok)throw Error('연결 코드 또는 서버 확인');const s=await r.json();out.textContent=s.status==='stale'?'결과 대기 / 연결 지연':(s.selection&&s.selection.status==='stable'?s.selection.detections.map(d=>names[d.label]+' '+Math.round(d.score*100)+'%').join('\n'):'중앙 후보 미확정 ('+(s.selection?s.selection.status:'구버전')+')')+'\n추론 '+s.infer_ms+'ms · 왕복 '+s.roundtrip_ms+'ms\n내 진행 신호: 미확정';}catch(e){out.textContent=e.message}setTimeout(poll,300)}if(token.value){active=true;poll()}</script></html>'''


class Server(ThreadingHTTPServer):
  daemon_threads = True
  def __init__(self, address, token, camera=None, results=None):
    self.token, self.camera = token, camera or Camera()
    self.auth = PairingAuth(token)
    self.results = results or Results(output='/dev/shm/eon_signal_lab.json')
    self.camera_slot = threading.Lock()
    self.clients = threading.BoundedSemaphore(6)
    super().__init__(address, Handler)

  def process_request(self, request, address):
    if not self.clients.acquire(False):
      self.shutdown_request(request)
      return
    try:
      super().process_request(request, address)
    except BaseException:
      self.clients.release()
      raise

  def process_request_thread(self, request, address):
    try:
      super().process_request_thread(request, address)
    finally:
      self.clients.release()


class Handler(BaseHTTPRequestHandler):
  protocol_version = 'HTTP/1.0'
  def setup(self):
    super().setup()
    self.connection.settimeout(3)

  def log_message(self, *_):
    pass

  def respond(self, status, body, kind='application/json', extra=None):
    if not isinstance(body, bytes):
      body = json.dumps(body, ensure_ascii=False).encode('utf-8')
    self.send_response(status)
    self.send_header('Content-Type', kind)
    self.send_header('Content-Length', str(len(body)))
    self.send_header('Cache-Control', 'no-store')
    self.send_header('X-Content-Type-Options', 'nosniff')
    for k, v in (extra or {}).items():
      self.send_header(k, str(v))
    self.end_headers()
    self.wfile.write(body)

  def authorized(self):
    status = self.server.auth.status(self.client_address[0], self.headers.get('Authorization', ''))
    if status != 200:
      self.respond(status, {'error': 'try again later' if status == 429 else 'invalid code'},
                   extra={'Retry-After': '30'} if status == 429 else None)
      return False
    return True

  def do_GET(self):
    if self.path == '/':
      return self.respond(200, PAGE.encode('utf-8'), 'text/html; charset=utf-8')
    if not self.authorized():
      return
    if self.path == '/state':
      return self.respond(200, self.server.results.snapshot())
    if self.path != '/frame.jpg':
      return self.respond(404, {'error': 'not found'})
    if not self.server.camera_slot.acquire(False):
      return self.respond(429, {'error': 'camera busy'})
    try:
      image, w, h = self.server.camera.frame()
      frame_id = self.server.results.issue()
      self.respond(200, image, 'image/jpeg', {'X-Signal-Frame': frame_id,
          'X-Signal-Session': self.server.results.session, 'X-Image-Width': w, 'X-Image-Height': h})
    except (RuntimeError, ImportError, OSError, ValueError) as error:
      self.respond(503, {'error': str(error)})
    finally:
      self.server.camera_slot.release()

  def do_POST(self):
    if not self.authorized():
      return
    if self.path != '/result':
      return self.respond(404, {'error': 'not found'})
    try:
      length = int(self.headers.get('Content-Length', '0'))
      if not 0 < length <= MAX_BODY:
        return self.respond(413, {'error': 'body too large or empty'})
      data = json.loads(self.rfile.read(length).decode('utf-8'))
      result = self.server.results.accept(data)
      self.respond(200, {'ok': True, 'roundtrip_ms': result['roundtrip_ms'], 'selection': result['selection']})
    except (ValueError, UnicodeError) as error:
      self.respond(400, {'error': str(error)})


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--host', default='0.0.0.0')
  parser.add_argument('--port', type=int, default=7799)
  parser.add_argument('--token-file', default=PAIRING_FILE)
  args = parser.parse_args()
  token = load_pairing_code(args.token_file)
  print('EON Signal Lab port:', args.port, flush=True)
  print('App connection code:', token, flush=True)
  print('Observer only. Pairing code is also shown in EON Web.', flush=True)
  # A manually launched old server may still hold the port during an update.
  # Keep this optional process alive and retry; it must not affect engagement.
  while True:
    try:
      server = Server((args.host, args.port), token)
    except OSError as error:
      print('Signal Lab waiting for port:', error, flush=True)
      time.sleep(5)
      continue
    try:
      server.serve_forever()
    finally:
      server.server_close()
    break


if __name__ == '__main__':
  main()
