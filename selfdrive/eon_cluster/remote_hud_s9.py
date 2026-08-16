"""S9-only remote HUD wrapper.

Keeps the existing low-overhead remote_hud transport, but exposes all existing
EON HUD Params to the Android renderer so the legacy HUD controls now tune the
S9 renderer live. The old direct-EON output selector is intentionally ignored.
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


def _packet(sm, atc_mode):
  packet = _original_packet(sm, atc_mode)

  # Existing EON HUD settings are now S9 runtime settings.
  packet["hudFps"] = _bounded_int("EonClusterHudFps", 8, 1, 15)
  packet["hudMapFps"] = _bounded_int("EonClusterHudMapFps", 5, 2, 5)
  packet["hudBrightness"] = _bounded_int("EonClusterHudBrightness", 65, 1, 100)
  packet["hudJpegQuality"] = _bounded_int("EonClusterHudJpegQuality", 55, 20, 95)
  packet["hudScreenMode"] = _bounded_int("EonClusterHudScreenMode", 1, 1, 3)
  packet["hudTheme"] = _bounded_int("EonClusterHudTheme", 0, 0, 2)
  packet["hudOrientation"] = _bounded_int("EonClusterHudOrientation", 0, 0, 2)
  packet["hudMirror"] = _bounded_int("EonClusterHudMirror", 0, 0, 1)
  packet["hudLanguage"] = _bounded_int("EonClusterHudLanguage", 0, 0, 1)
  packet["hudRadarInfo"] = _bounded_int("EonClusterHudRadarInfo", 4, 0, 4)
  return packet


def _remote_output_enabled(params):
  # External HUD is S9-only now; the legacy direct-EON output mode is ignored.
  return params.get_bool(base.PARAM_ENABLED)


base._packet = _packet
base._remote_output_enabled = _remote_output_enabled


if __name__ == "__main__":
  base.main()
