#!/usr/bin/env python3
"""guidance_next distance: control keeps the vehicle distance, the HUD row shows the phone's segment."""
import sys
import types
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
try:
  import common.params  # noqa: F401,E402
except ImportError:
  params_module = types.ModuleType("common.params")
  params_module.Params = object
  sys.modules["common.params"] = params_module

from selfdrive import carrot_navi_server as server  # noqa: E402


def state_for(source):
  state = server.NaviState()
  state._configured_source = lambda: source
  return state


def main():
  # TMAP: both distances are from the vehicle; TMAP's own popup shows the difference.
  tmap = state_for(server.SOURCE_TMAP)
  tmap.update(server.SOURCE_TMAP, "guidance_current", {"turn_type": 12, "distance_m": 622})
  tmap.update(server.SOURCE_TMAP, "guidance_next", {"turn_type": 13, "distance_m": 1502})
  assert tmap.values["guidance_next"]["distance_m"] == 1502
  assert tmap.values["guidance_next"]["display_distance_m"] == 880

  # NAVER keeps its existing correction of distance_m itself.
  naver = state_for(server.SOURCE_NAVER)
  naver.update(server.SOURCE_NAVER, "guidance_current", {"turn_type": 12, "distance_m": 622})
  naver.update(server.SOURCE_NAVER, "guidance_next", {"turn_type": 13, "distance_m": 1502})
  assert naver.values["guidance_next"]["distance_m"] == 880
  assert "display_distance_m" not in naver.values["guidance_next"]

  # Kakao already sends the segment distance.
  kakao = state_for(server.SOURCE_KAKAO)
  kakao.update(server.SOURCE_KAKAO, "guidance_current", {"turn_type": 12, "distance_m": 622})
  kakao.update(server.SOURCE_KAKAO, "guidance_next", {"turn_type": 13, "distance_m": 880})
  assert kakao.values["guidance_next"] == {"turn_type": 13, "distance_m": 880}
  print("next-turn distance checks passed for TMAP, NAVER and Kakao")


if __name__ == "__main__":
  main()
