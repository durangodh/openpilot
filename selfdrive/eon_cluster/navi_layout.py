"""S9 HUD 네비모드(지도 8 : 주행 2) 선택.

EonClusterHudNaviLayout 0=주행모드, 1=네비모드, 2=자동(길안내 중 네비모드).
remote_hud_s9 가 실제로 쓸 배치를 정해 HUD 패킷(hudNaviLayout)에 싣고 같은 값을
STATE_FILE 에 남긴다. carrot_navi_server 는 그 값을 1초 탐색 방송(hud_wide_map)으로
휴대폰 모듈에 알려, 네비모드에서는 지도를 가로로 넓게 캡처하게 한다.
"""
import os
import time

STATE_FILE = "/dev/shm/hud_navi_layout"
# 재탐색·안내 갱신 중 잠깐 안내가 비어도 배치가 오가지 않게 한다.
AUTO_RELEASE_S = 5.0
# remote_hud_s9 가 멈추면(파일이 오래되면) 휴대폰은 기본 캡처로 돌아간다.
STATE_MAX_AGE_S = 5.0


class NaviLayout:
  def __init__(self):
    self.active = False
    self.last_guiding = 0.0
    self.last_written = None
    self.last_write_time = 0.0

  def update(self, setting, guiding, now=None):
    now = time.monotonic() if now is None else now
    if guiding:
      self.last_guiding = now
    if setting == 1:
      self.active = True
    elif setting == 2:
      self.active = guiding or (self.active and now - self.last_guiding < AUTO_RELEASE_S)
    else:
      self.active = False
    return 1 if self.active else 0

  def publish(self, value, now=None):
    now = time.monotonic() if now is None else now
    if value == self.last_written and now - self.last_write_time < 1.0:
      return
    try:
      tmp = STATE_FILE + ".tmp"
      with open(tmp, "w") as f:
        f.write(str(value))
      os.rename(tmp, STATE_FILE)
      self.last_written = value
      self.last_write_time = now
    except OSError:
      pass


def read_wide_map():
  """휴대폰 모듈에 알릴 값. 파일이 없거나 오래되면 0(기본 캡처)."""
  try:
    if time.time() - os.path.getmtime(STATE_FILE) > STATE_MAX_AGE_S:
      return 0
    with open(STATE_FILE) as f:
      return 1 if f.read().strip() == "1" else 0
  except (OSError, ValueError):
    return 0
