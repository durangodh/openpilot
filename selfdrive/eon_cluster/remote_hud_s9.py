"""S9-only remote HUD wrapper.

Keeps the existing low-overhead remote_hud transport, but exposes the existing
EON HUD Params to the Android renderer so FPS/JPEG quality/brightness can be
changed live from the EON settings UI without rebuilding the APK.
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
  packet["hudFps"] = _bounded_int("EonClusterHudFps", 8, 1, 15)
  packet["hudBrightness"] = _bounded_int("EonClusterHudBrightness", 65, 1, 100)
  packet["hudJpegQuality"] = _bounded_int("EonClusterHudJpegQuality", 55, 20, 95)
  return packet


def _remote_output_enabled(params):
  # External HUD is S9-only now; the legacy direct-EON output mode is ignored.
  return params.get_bool(base.PARAM_ENABLED)


base._packet = _packet
base._remote_output_enabled = _remote_output_enabled


if __name__ == "__main__":
  base.main()
