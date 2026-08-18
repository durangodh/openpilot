"""S9-only remote HUD wrapper.

Keeps the existing low-overhead remote_hud transport and exposes the S9 HUD
Params to the Android renderer for live tuning.
"""

from common.params import Params
from selfdrive.eon_cluster import remote_hud as base


_params = Params()
_original_packet = base._packet


def _bounded_int(key, default, minimum, maximum):
  try:
    raw = _params.get(key)
    value = int(raw) if raw is not None else default
  except (TypeError, ValueError):
    value = default
  return max(minimum, min(maximum, value))


def _packet(sm, *args, **kwargs):
  # base._packet 의 인자가 늘어나도(atc_mode → +path_offset 등) 그대로
  # 흘려보낸다. 고정 인자로 받으면 base 쪽 시그니처가 바뀔 때마다
  # TypeError 로 패킷이 아예 안 나가고 폰에는 "EON 연결 끊김" 만 뜬다.
  packet = _original_packet(sm, *args, **kwargs)

  # Preserve 0 for FPS (pause) and brightness (auto), matching the UI.
  packet["hudFps"] = _bounded_int("EonClusterHudFps", 8, 0, 15)
  packet["hudMapFps"] = _bounded_int("EonClusterHudMapFps", 5, 2, 5)
  packet["hudBrightness"] = _bounded_int("EonClusterHudBrightness", 65, 0, 100)
  packet["hudJpegQuality"] = _bounded_int("EonClusterHudJpegQuality", 55, 20, 95)
  packet["hudScreenMode"] = _bounded_int("EonClusterHudScreenMode", 1, 1, 3)
  packet["hudTheme"] = _bounded_int("EonClusterHudTheme", 0, 0, 2)
  packet["hudOrientation"] = _bounded_int("EonClusterHudOrientation", 0, 0, 2)
  packet["hudMirror"] = _bounded_int("EonClusterHudMirror", 0, 0, 1)
  packet["hudLanguage"] = _bounded_int("EonClusterHudLanguage", 0, 0, 1)
  packet["hudRadarInfo"] = _bounded_int("EonClusterHudRadarInfo", 4, 0, 4)
  packet["hudBuildings"] = _bounded_int("EonClusterHudBuildings", 1, 0, 1)
  packet["hudOutputMode"] = _bounded_int("EonClusterHudOutputMode", 1, 1, 3)
  packet["hudBsdStyle"] = _bounded_int("EonClusterHudBsdStyle", 2, 1, 3)
  packet["hudCarStyle"] = _bounded_int("EonClusterHudCarStyle", 1, 1, 2)
  packet["hudRoadSigns"] = _bounded_int("EonClusterHudRoadSigns", 3, 0, 3)
  return packet


base._packet = _packet

# manager(selfdrive/manager/process.py) 는 importlib 로 모듈을 불러
# mod.main() 을 호출한다. __name__ 이 "__main__" 이 아니므로 아래
# 블록만으로는 절대 실행되지 않는다.
main = base.main


if __name__ == "__main__":
  base.main()
