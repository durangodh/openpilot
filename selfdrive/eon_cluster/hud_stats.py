"""HUD 지도 경로 통계: 10초마다 한 줄씩 남긴다(멈춤·밀림이 어느 구간인지 찾는 용도).

carrot_navi_server(폰 → EON 수신)와 remote_hud(EON → S9 전송)가 각자 파일에 쓴다.
한 줄 약 150바이트, 10초에 한 번이라 시간당 약 54KB. 폴더 전체 2MB 를 넘으면 오래된
파일부터 지운다. 폰 브라우저 http://EON_IP:7714/trace 의 "HUD 지도 통계"에서 받는다.
기록 실패는 무시한다(지도 전달에 영향 없음).
"""
import os
import threading
import time

STATS_DIR = "/data/media/0/hud_trace"
INTERVAL_S = 10.0
MAX_TOTAL_BYTES = 2 * 1024 * 1024


class GapStats(object):
  """이벤트 개수와 이벤트 사이 최대 간격(초)을 창 단위로 센다."""

  def __init__(self):
    self.count = 0
    self.last = 0.0
    self.max_gap = 0.0
    self.max_value = 0.0

  def mark(self, now, value=0.0):
    if self.last > 0.0:
      self.max_gap = max(self.max_gap, now - self.last)
    self.last = now
    self.count += 1
    self.max_value = max(self.max_value, value)

  def take(self, now):
    # 창 끝까지 이벤트가 없었으면 그 공백도 최대 간격에 넣는다.
    gap = max(self.max_gap, now - self.last) if self.last > 0.0 else 0.0
    out = (self.count, gap, self.max_value)
    self.count = 0
    self.max_gap = 0.0
    self.max_value = 0.0
    return out


class StatsLog(object):
  def __init__(self, prefix):
    self.prefix = prefix
    self.lock = threading.Lock()
    self.window_start = time.monotonic()
    self.counters = {}
    self.gaps = {}

  def add(self, key, n=1):
    with self.lock:
      self.counters[key] = self.counters.get(key, 0) + n

  def mark(self, key, value=0.0):
    now = time.monotonic()
    with self.lock:
      self.gaps.setdefault(key, GapStats()).mark(now, value)

  def maybe_flush(self, extra=""):
    now = time.monotonic()
    with self.lock:
      if now - self.window_start < INTERVAL_S:
        return
      parts = []
      for key in sorted(self.gaps):
        count, gap, value = self.gaps[key].take(now)
        part = "%s %d gapmax %.2fs" % (key, count, gap)
        if value > 0.0:
          part += " max %.0fms" % (value * 1000.0)
        parts.append(part)
      for key in sorted(self.counters):
        parts.append("%s %d" % (key, self.counters[key]))
      self.counters = {}
      window = now - self.window_start
      self.window_start = now
    line = "%s %.0fs | %s%s\n" % (time.strftime("%H:%M:%S"), window, " | ".join(parts),
                                  (" | " + extra) if extra else "")
    try:
      os.makedirs(STATS_DIR, exist_ok=True)
      path = os.path.join(STATS_DIR, "%s_%s.log" % (self.prefix, time.strftime("%Y%m%d")))
      new_file = not os.path.exists(path)
      with open(path, "a") as f:
        f.write(line)
      if new_file:
        _rotate()
    except (IOError, OSError):
      pass


def _rotate():
  try:
    files = sorted((os.path.join(STATS_DIR, f) for f in os.listdir(STATS_DIR) if f.endswith(".log")),
                   key=os.path.getmtime)
    total = sum(os.path.getsize(f) for f in files)
    while len(files) > 1 and total > MAX_TOTAL_BYTES:
      oldest = files.pop(0)
      total -= os.path.getsize(oldest)
      os.remove(oldest)
  except OSError:
    pass


MODULE_LOG_SOURCES = ("kakao", "naver", "tmap", "gps")


def append_module_log(source, text):
  """Navigation module log line sent over the state socket (e.g. Kakao voice button).

  Written to STATS_DIR/<source>_YYYYMMDD.log so the /trace page lists it next to
  the HUD stats. Only known sources, one line, at most 500 characters.
  """
  if source not in MODULE_LOG_SOURCES or not isinstance(text, str):
    return
  text = text.replace("\r", " ").replace("\n", " ")[:500]
  try:
    os.makedirs(STATS_DIR, exist_ok=True)
    path = os.path.join(STATS_DIR, "%s_%s.log" % (source, time.strftime("%Y%m%d")))
    new_file = not os.path.exists(path)
    with open(path, "a") as f:
      f.write("%s %s\n" % (time.strftime("%H:%M:%S"), text))
    if new_file:
      _rotate()
  except (IOError, OSError):
    pass
