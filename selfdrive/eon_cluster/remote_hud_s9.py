"""S9-only remote HUD wrapper.

Keeps the existing low-overhead remote_hud transport and exposes the S9 HUD
Params to the Android renderer for live tuning.
"""

import math
import time

from common.params import Params, UnknownKeyName
from selfdrive.eon_cluster import remote_hud as base
from selfdrive.eon_cluster.hud_geometry import normalize_geometry
from selfdrive.controls.lib.navigation_route import NavigationRouteData


_params = Params()
_nav_route = NavigationRouteData()
_original_packet = base._packet
_param_cache = {}
PARAM_CACHE_S = 1.0


def _bounded_int(key, default, minimum, maximum):
  now = time.monotonic()
  cached = _param_cache.get(key)
  if cached is not None and now - cached[0] < PARAM_CACHE_S:
    value = cached[1]
  else:
    try:
      raw = _params.get(key)
      value = int(raw) if raw is not None else default
    except (TypeError, ValueError, UnknownKeyName):
      # New HUD tuning keys may not exist in older EON Params registries.
      # Keep the feature default instead of aborting the entire UDP packet.
      value = default
    _param_cache[key] = (now, value)
  return max(minimum, min(maximum, value))


def _apply_path_flip(packet):
  # Normalize source axes once; the Android projector reflects the whole scene.
  return normalize_geometry(packet, _bounded_int("EonClusterHudPathFlip", 0, 0, 1))


def _apply_naver_speed(packet):
  """Project NAVER SDI into the existing HUD fields only when NAVER is selected.

  TMAP keeps base._packet's original roadLimitSpeed values byte-for-byte.
  """
  if _bounded_int("EonClusterHudNavApp", 1, 1, 2) != 2:
    return packet

  state = _nav_route.update()
  events = _nav_route.speed_events(state)
  camera = events.get("camera")
  section = events.get("section")
  packet["limit"] = max(0, int(round(float(state.get("road_limit_kph", 0.0) or 0.0))))
  packet["camera"] = 0
  packet["cameraDist"] = 0
  packet["cameraSection"] = False
  packet["bumpDist"] = 0

  if isinstance(camera, dict):
    event_type = int(camera.get("type", 0) or 0)
    distance = max(0, int(round(float(camera.get("distance", 0.0) or 0.0))))
    limit = max(0, int(round(float(camera.get("limit", 0.0) or 0.0))))
    if event_type == 22:
      packet["bumpDist"] = distance
    else:
      packet["camera"] = limit
      packet["cameraDist"] = distance
  elif isinstance(section, dict):
    packet["camera"] = max(0, int(round(float(section.get("limit", 0.0) or 0.0))))
    packet["cameraDist"] = max(0, int(round(float(section.get("distance", 0.0) or 0.0))))
    packet["cameraSection"] = packet["camera"] > 0 and packet["cameraDist"] > 0
  return packet

def _packet(sm, *args, **kwargs):
  # base._packet 의 인자가 늘어나도(noo_enabled → +path_offset 등) 그대로
  # 흘려보낸다. 고정 인자로 받으면 base 쪽 시그니처가 바뀔 때마다
  # TypeError 로 패킷이 아예 안 나가고 폰에는 "EON 연결 끊김" 만 뜬다.
  packet = _original_packet(sm, *args, **kwargs)
  packet = _apply_path_flip(packet)
  packet = _apply_naver_speed(packet)

  view_pitch = _bounded_int("EonClusterHudViewPitch", 0, -50, 50)
  calibrated_pitch = float(packet.get("calibPitch", 0.0) or 0.0) + math.radians(view_pitch * 0.1)
  packet["calibPitch"] = round(max(-0.15, min(0.15, calibrated_pitch)), 4)
  packet["hudViewPitch"] = view_pitch

  # Preserve 0 for FPS (pause) and brightness (auto), matching the UI.
  packet["hudFps"] = _bounded_int("EonClusterHudFps", 7, 0, 15)
  packet["hudMapFps"] = _bounded_int("EonClusterHudMapFps", 3, 2, 5)
  packet["hudBrightness"] = _bounded_int("EonClusterHudBrightness", 0, 0, 100)
  packet["hudDayBrightness"] = _bounded_int("EonClusterHudDayBrightness", 65, 1, 100)
  packet["hudNightBrightness"] = _bounded_int("EonClusterHudNightBrightness", 35, 1, 100)
  packet["hudJpegQuality"] = _bounded_int("EonClusterHudJpegQuality", 55, 20, 95)
  packet["hudScreenMode"] = _bounded_int("EonClusterHudScreenMode", 1, 1, 3)
  packet["hudTheme"] = _bounded_int("EonClusterHudTheme", 0, 0, 2)
  packet["hudOrientation"] = _bounded_int("EonClusterHudOrientation", 0, 0, 2)
  packet["hudMirror"] = _bounded_int("EonClusterHudMirror", 0, 0, 1)
  packet["hudLanguage"] = _bounded_int("EonClusterHudLanguage", 0, 0, 1)
  packet["hudRadarInfo"] = _bounded_int("EonClusterHudRadarInfo", 4, 0, 4)
  # The EON sends only a 320x240 JPEG preview. Detection is CPU TFLite on S9,
  # display-only, and never returns to the driving stack.
  packet["hudPhoneVision"] = _bounded_int("EonClusterHudVisionDetector", 0, 0, 1)
  packet["hudVisionFps"] = _bounded_int("EonClusterHudVisionDetectorFps", 3, 1, 3)
  # Favour scene coverage: even an older persisted 55% value is capped at
  # 45%, while the UI can still lower the threshold to 25% in difficult light.
  packet["hudVisionThreshold"] = _bounded_int("EonClusterHudVisionDetectorThreshold", 40, 25, 45)
  # 노면 높낮이 배율(%). 100=원본, 0=평지. 모델 z 의 부호가 기기마다 다를 수
  # 있어 음수까지 열어둔다 — 오르막이 아래로 꺼져 보이면 -100 으로 뒤집는다.
  packet["hudRoadZ"] = _bounded_int("EonClusterHudRoadZ", 100, -300, 300)
  # 4차: 모델 도로를 바꾸지 않고, 근거리에서 modelV2와 일치할 때만
  # 티맵 경로 의도를 반투명 선으로 표시한다. 0이면 즉시 숨긴다.
  packet["hudNavRoute"] = _bounded_int("EonClusterHudNavRoute", 1, 0, 1)
  # 주행 중 차량 pitch 를 수평선에 반영하는 정도(%). 0=끄기(정적 캘리브만).
  packet["hudPitchDyn"] = _bounded_int("EonClusterHudPitchDyn", 60, 0, 200)
  packet["hudOutputMode"] = _bounded_int("EonClusterHudOutputMode", 1, 1, 3)
  packet["hudLayoutMode"] = _bounded_int("EonClusterHudLayoutMode", 1, 1, 2)
  # EON에서 선택한 내비를 S9 Remote HUD가 실행한다. nMirror 본체는 원본
  # 서명을 유지하므로 재패키징 뒤 종료되는 문제를 만들지 않는다.
  packet["hudNavApp"] = _bounded_int("EonClusterHudNavApp", 1, 1, 2)
  # 모델 도로경계(edges) 위에만 세우는 가드레일. 경계가 없으면 안 그려진다.
  packet["hudGuardrail"] = _bounded_int("EonClusterHudGuardrail", 1, 0, 1)
  # 지평선 근처 원경 페이드 강도(%). 0 이면 끔.
  packet["hudHaze"] = _bounded_int("EonClusterHudHaze", 55, 0, 100)
  # 0(기본): 앱 내장 화살표 그림 사용
  # 1: 티맵 tbt_current_compact 사용 — 다만 이 스트림은 아이콘이 아니라
  #    "화살표+거리+도로명"이 한 장에 그려진 미니 배너라 아이콘 자리에 넣으면
  #    검은 박스나 배너 중복으로 보인다. 실물을 확인한 뒤에만 켤 것.
  packet["hudTmapIcon"] = _bounded_int("EonClusterHudTmapIcon", 0, 0, 1)
  # 0: 끔 / 1: 분기 실사 이미지 / 2: 실사 + 하단 도착정보 바
  packet["hudJunction"] = _bounded_int("EonClusterHudJunction", 2, 0, 2)
  return packet


base._packet = _packet

# manager(selfdrive/manager/process.py) 는 importlib 로 모듈을 불러
# mod.main() 을 호출한다. __name__ 이 "__main__" 이 아니므로 아래
# 블록만으로는 절대 실행되지 않는다.
main = base.main


if __name__ == "__main__":
  base.main()
