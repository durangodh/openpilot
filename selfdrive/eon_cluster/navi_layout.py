"""S9 HUD 네비모드(지도 8 : 주행 2) 선택.

EonClusterHudNaviLayout 0=주행모드, 1=네비모드, 2=자동(길안내 중 네비모드).
remote_hud_s9 가 실제로 쓸 배치를 정해 HUD 패킷(hudNaviLayout)에 싣고 같은 값을
STATE_FILE 에 남긴다. carrot_navi_server 는 그 값을 1초 탐색 방송(hud_wide_map)으로
휴대폰 모듈에 알려, 네비모드에서는 지도를 가로로 넓게 캡처하게 한다.

EonClusterHudMapScale{Tmap,Kakao,Naver}: 네비모드 HUD 지도 크기(휴대폰 앱 원본 축척 대비 %).
같은 방송(hud_map_scale_tmap/kakao/naver)으로 각 휴대폰 모듈에 알린다.
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


def read_wide_map(params=None):
  """휴대폰 모듈에 알릴 값. 파일이 없거나 오래되면 0(기본 캡처).

  설정이 1(네비모드 고정)이면 HUD 연결 전에도 1. 부팅 직후 remote_hud_s9 가 STATE_FILE 을
  쓰기 전에 휴대폰 앱이 먼저 떠서 주행모드 크기 지도로 시작했고, HUD 가 그걸 늘려 첫 화면이
  아주 크게 보였다(2026-10-14, 다른 네비를 고르면 정상).
  """
  if params is not None:
    try:
      if int((params.get("EonClusterHudNaviLayout") or b"0").decode() or 0) == 1:
        return 1
    except (ValueError, AttributeError):
      pass
  try:
    if time.time() - os.path.getmtime(STATE_FILE) > STATE_MAX_AGE_S:
      return 0
    with open(STATE_FILE) as f:
      return 1 if f.read().strip() == "1" else 0
  except (OSError, ValueError):
    return 0


# 키 → (방송 이름, 기본 %). 범위는 30~200(100 = 휴대폰 앱 원본 축척).
MAP_SCALE_KEYS = (
  ("EonClusterHudMapScaleTmap", "hud_map_scale_tmap", 70),
  ("EonClusterHudMapScaleKakao", "hud_map_scale_kakao", 70),
  ("EonClusterHudMapScaleNaver", "hud_map_scale_naver", 70),
)


def read_map_scales(params):
  """방송에 실을 앱별 네비모드 지도 크기(%). 읽지 못하면 기본값."""
  out = {}
  for key, field, default in MAP_SCALE_KEYS:
    try:
      value = int(float((params.get(key) or b"").decode() or default))
    except (ValueError, AttributeError):
      value = default
    out[field] = max(30, min(200, value))
  return out
