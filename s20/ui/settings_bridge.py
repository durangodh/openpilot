"""
Settings for the S20 screen app, like the comma 3X settings panels (selfdrive/ui/layouts/settings).
The app (Android side) cannot reach openpilot's params, so it drops small JSON commands into /s20share/cmd/;
s20uid applies them here and reports the current values back in ui.json ("settings").
Only the keys below can be changed, with the same rules as the comma UI (restart toggles and calibration reset
only while disengaged, the debug modes exclude each other).
"""
import json, os

BOOL_KEYS = {
  # toggles panel
  "OpenpilotEnabledToggle", "DisengageOnAccelerator", "IsLdwEnabled", "IsMetric",
  # developer panel
  "ShowDebugInfo", "JoystickDebugMode", "LongitudinalManeuverMode", "LateralManeuverMode", "AlphaLongitudinalEnabled",
}
INT_KEYS = {"LongitudinalPersonality": (0, 2), "InitialCruiseSpeedKph": (40, 145)}  # aggressive/standard/relaxed; V_CRUISE_INITIAL..MAX
RESTART_KEYS = {"OpenpilotEnabledToggle", "AlphaLongitudinalEnabled"}  # comma UI: changing them restarts openpilot onroad
EXCLUSIVE = ("JoystickDebugMode", "LongitudinalManeuverMode", "LateralManeuverMode")
CALIB_KEYS = ("CalibrationParams", "LiveTorqueParameters", "LiveParametersV2", "LiveDelay")
TEXT_KEYS = ("DongleId", "HardwareSerial", "Version", "GitBranch", "GitCommit", "GitCommitDate")
MAX_CMDS = 20


class SettingsBridge:
  def __init__(self, params, cmd_dir: str):
    self.params, self.cmd_dir = params, cmd_dir
    self.last = {"id": "", "ok": True, "msg": ""}
    try:
      os.makedirs(cmd_dir, exist_ok=True)
      os.chmod(cmd_dir, 0o777)  # the app (another uid) writes here
    except OSError:
      pass

  def _text(self, k: str) -> str:
    try:
      v = self.params.get(k)
    except Exception:
      return ""
    return v.decode() if isinstance(v, bytes) else ("" if v is None else str(v))

  def values(self) -> dict:
    out: dict = {}
    for k in sorted(BOOL_KEYS):
      try:
        out[k] = bool(self.params.get_bool(k))
      except Exception:
        out[k] = False
    for k, (lo, _) in INT_KEYS.items():
      try:
        v = self.params.get(k, return_default=True)
        out[k] = int(v) if v is not None else lo
      except Exception:
        out[k] = lo
    for k in TEXT_KEYS:
      out[k] = self._text(k)
    try:
      out["IsReleaseBranch"] = bool(self.params.get_bool("IsReleaseBranch"))
    except Exception:
      out["IsReleaseBranch"] = False
    return out

  def apply(self, cmd: dict, engaged: bool) -> tuple[bool, str]:
    op, key, value = cmd.get("op"), cmd.get("key"), cmd.get("value")
    if op == "reset_calib":
      if engaged:
        return False, "해제 후 캘리브레이션을 초기화하세요"
      for k in CALIB_KEYS:
        self.params.remove(k)
      self.params.put_bool("OnroadCycleRequested", True)
      return True, "캘리브레이션 초기화됨"
    if op == "put_bool" and key in BOOL_KEYS:
      if key in RESTART_KEYS and engaged:
        return False, "해제 후 바꾸세요 (오파가 다시 시작되는 설정)"
      state = bool(value)
      self.params.put_bool(key, state)
      if key in EXCLUSIVE and state:
        for other in EXCLUSIVE:
          if other != key:
            self.params.put_bool(other, False)
      if key in RESTART_KEYS:
        self.params.put_bool("OnroadCycleRequested", True)
      return True, ""
    if op == "put_int" and key in INT_KEYS:
      lo, hi = INT_KEYS[key]
      try:
        v = int(value)
      except (TypeError, ValueError):
        return False, f"잘못된 값 {value!r}"
      if not lo <= v <= hi:
        return False, f"{key}: {lo}~{hi} 범위 밖 ({v})"
      self.params.put(key, v)
      return True, ""
    return False, f"허용되지 않은 명령 {op} {key}"

  def poll(self, engaged: bool) -> None:
    try:
      names = sorted(n for n in os.listdir(self.cmd_dir) if n.endswith(".json"))
    except OSError:
      return
    for n in names[:MAX_CMDS]:
      p = os.path.join(self.cmd_dir, n)
      try:
        with open(p) as f:
          cmd = json.load(f)
        ok, msg = self.apply(cmd if isinstance(cmd, dict) else {}, engaged)
      except Exception as e:  # a half-written or bad file: drop it
        ok, msg, cmd = False, f"명령 읽기 실패: {e}", {}
      try:
        os.remove(p)
      except OSError:
        pass
      self.last = {"id": str(cmd.get("id", n)), "ok": ok, "msg": msg}
