"""Setting lookup and change requests from the S20 HUD voice assistant (HUDPRM1).

The phone asks over the existing 7210 UDP link; the reply goes straight back to the
sender. Only settings listed in selfdrive/apilot.json (the APM list) can be read or
written, every write is range-checked here, and while the car moves only the cruise and
longitudinal groups can change. Whatever the AI on the phone asks for, these rules hold.

Request : HUDPRM1 {"id": "...", "op": "direct"|"direct_set"|"search"|"get"|"set"|"press"|
                                   "system_check"|"system"|"system_status", ...}
          system ops are git pull / reboot (voice_system.py: parked or off only).
          press only accepts cmd "gap" (차간 단계 = 크루즈 GAP 버튼 상태, 설정값 아님).
Reply   : HUDPRM1R {"id": "...", "ok": true|false, ...}
"""
import json
import os
import re
import unicodedata

PREFIX = b"HUDPRM1 "
REPLY_PREFIX = b"HUDPRM1R "
APILOT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apilot.json")
# 주행 중 변경 가능한 그룹(가감속·차간·크루즈). 횡방향·화면·기본 설정은 정차 중에만 바꾼다.
DRIVING_GROUPS = ("크루즈", "종방향")
SEARCH_LIMIT = 8
DESCR_MAX = 90
REPLY_MAX_BYTES = 6000


DIRECT_ALIASES = {
  "최저 크루즈 속도": "CruiseSpeedMin",
  "최소 크루즈 속도": "CruiseSpeedMin",
  "고정 조향비": "SteerRatioApply",
  "학습 조향비 적용 비율": "LiveSteerRatioApply",
  "조향 반응 지연": "SteerActuatorDelay",
  "스티어링 레이트 코스트": "SteeringRateCost",
  "커스텀 토크": "LateralTorqueCustom",
  "토크 팩터": "LateralTorqueAccelFactor",
  "마찰값": "LateralTorqueFriction",
  "허드 밝기": "EonClusterHudBrightness",
  "허드 프레임": "EonClusterHudFps",
  "출발 가속도": "StartAccelApply",
  "정지 감속도": "StopAccelApply",
}

DIRECT_WRITE = re.compile(r"(.+?)\s+(-?\d+)\s*(?:으로|로)\s*(?:변경|바꿔|설정)(?:해(?:줘|주세요)?)?")
DIRECT_READ = re.compile(r"(.+?)(?:\s*(?:설정값|현재값|현재 값))?\s*(?:얼마야|몇이야|몇이예요|알려줘|보여줘|조회(?:해줘)?|설명해줘)")
DIRECT_SEARCH = re.compile(r"(.+?)\s*(?:설정\s*)?(?:찾아줘|검색(?:해줘)?)")


def _spoken_key(text):
  return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold() if ch.isalnum())


def load_definitions(path=APILOT_JSON):
  try:
    with open(path, encoding="utf-8") as f:
      params = json.load(f).get("params", [])
  except (OSError, ValueError, AttributeError):
    return {}
  out = {}
  for p in params:
    if isinstance(p, dict) and isinstance(p.get("name"), str):
      out[p["name"]] = p
  return out


def _int(raw, default):
  try:
    if isinstance(raw, bytes):
      raw = raw.decode("utf-8", "replace")
    return int(float(str(raw).strip()))
  except (TypeError, ValueError):
    return default


class VoiceParams:
  def __init__(self, params, definitions=None):
    self.params = params
    self.definitions = load_definitions() if definitions is None else definitions

  def _value(self, d):
    try:
      raw = self.params.get(d["name"])
    except Exception:
      raw = None
    return _int(raw, d.get("default", 0)) if raw not in (None, b"", "") else d.get("default", 0)

  def _card(self, d, with_value=True, short=False):
    descr = str(d.get("descr", ""))
    card = {"name": d["name"], "title": d.get("title", ""), "group": d.get("group", ""),
            "descr": descr[:DESCR_MAX] if short else descr,
            "min": d.get("min"), "max": d.get("max"), "default": d.get("default"),
            "step": d.get("unit", 1),
            "driving_change": d.get("group") in DRIVING_GROUPS}
    if with_value:
      card["value"] = self._value(d)
    return card

  def search(self, keywords):
    words = [str(w).strip().lower() for w in (keywords or []) if str(w).strip()][:8]
    if not words:
      return {"ok": False, "error": "검색어 없음"}
    scored = []
    for d in self.definitions.values():
      name = d["name"].lower()
      title = str(d.get("title", "")).lower()
      descr = str(d.get("descr", "")).lower()
      score = 0
      for w in words:
        if w == name:
          score += 10
        elif w in name:
          score += 4
        if w in title:
          score += 3
        if w in descr:
          score += 1
      if score:
        scored.append((score, d["name"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return {"ok": True, "results": [self._card(self.definitions[n], short=True)
                                    for _, n in scored[:SEARCH_LIMIT]]}

  def get(self, name):
    d = self.definitions.get(name)
    if d is None:
      return {"ok": False, "error": "목록에 없는 설정: %s" % name}
    return {"ok": True, "setting": self._card(d)}

  def set(self, name, value, moving):
    d = self.definitions.get(name)
    if d is None:
      return {"ok": False, "error": "목록에 없는 설정: %s" % name}
    if moving and d.get("group") not in DRIVING_GROUPS:
      return {"ok": False, "error": "주행 중에는 바꿀 수 없는 설정(%s 그룹). 정차 후 다시 요청" % d.get("group", "")}
    target = _int(value, None)
    if target is None:
      return {"ok": False, "error": "정수 값이 아님: %s" % value}
    lo, hi = d.get("min"), d.get("max")
    if (isinstance(lo, (int, float)) and target < lo) or (isinstance(hi, (int, float)) and target > hi):
      return {"ok": False, "error": "범위 밖 값 %d (허용 %s~%s)" % (target, lo, hi)}
    before = self._value(d)
    try:
      self.params.put(name, str(target))
    except Exception as e:
      return {"ok": False, "error": "저장 실패: %s" % e}
    after = self._value(d)
    return {"ok": after == target, "name": name, "title": d.get("title", ""),
            "before": before, "after": after, "min": lo, "max": hi,
            "error": "" if after == target else "저장값 확인 실패"}

  # AI 가 누를 수 있는 크루즈 버튼: 차간(GAP)과 설정속도(RES/SET, 길게=10 단위).
  # 취소(CANCEL)·차선변경은 운전자 몫이라 넣지 않는다.
  AI_BUTTONS = ("gap", "res", "set", "res_long", "set_long")

  @staticmethod
  def press(command, press):
    """Cruise button for the AI (gap and set speed only)."""
    if command not in VoiceParams.AI_BUTTONS:
      return {"ok": False, "error": "AI 는 GAP·설정속도 버튼만 누를 수 있음"}
    if press is None:
      return {"ok": False, "error": "버튼 경로 없음"}
    result = press(command)
    return {"ok": result == "accepted", "result": result,
            "error": "" if result == "accepted" else "지금은 버튼을 보낼 수 없음(시동·차량 데이터 확인)"}

  def direct(self, text):
    if not isinstance(text, str) or not 0 < len(text) <= 180:
      return {"ok": False, "handled": True, "error": "음성 문장을 확인하세요."}
    text = unicodedata.normalize("NFKC", text).strip().rstrip("?.!。 ")
    write = DIRECT_WRITE.fullmatch(text)
    read = DIRECT_READ.fullmatch(text)
    search = DIRECT_SEARCH.fullmatch(text)
    if not (write or read or search):
      return {"ok": False, "handled": False}
    phrase = (write or read or search).group(1).strip()
    phrase = re.sub(r"\s*(?:설정값|설정|값)(?:을|를)?$", "", phrase).strip()
    if phrase.endswith(("을", "를")):
      phrase = phrase[:-1].strip()
    normalized = _spoken_key(phrase)
    names = {name for name, definition in self.definitions.items()
             if normalized and normalized in (_spoken_key(name), _spoken_key(definition.get("title", "")))}
    names.update(name for alias, name in DIRECT_ALIASES.items()
                 if normalized == _spoken_key(alias) and name in self.definitions)
    if write:
      if len(names) != 1:
        return {"ok": False, "handled": True, "error": "바꿀 설정을 정확히 말해주세요."}
      name = next(iter(names))
      definition = self.definitions[name]
      value = int(write.group(2))
      lo, hi = definition.get("min"), definition.get("max")
      if type(lo) is not int or type(hi) is not int or not lo <= value <= hi:
        return {"ok": False, "handled": True, "error": "허용 범위 밖의 값입니다."}
      return {"ok": True, "handled": True, "proposal": self._card(definition), "target": value}
    if len(names) == 1:
      return dict(self.get(next(iter(names))), handled=True, explain=text.endswith("설명해줘"))
    result = self.search([phrase] + phrase.split())
    return dict(result, handled=True) if result.get("results") or "설정" in text else {"ok": False, "handled": False}

  def handle(self, data, moving, press=None, system=None, system_state=None, offroad=False, onroad=False):
    """Raw HUDPRM1 datagram -> reply bytes, or None when it is not ours or malformed."""
    if not data.startswith(PREFIX):
      return None
    try:
      req = json.loads(data[len(PREFIX):].decode("utf-8"))
      rid = str(req.get("id", ""))[:40]
      op = req.get("op")
    except (ValueError, AttributeError, UnicodeDecodeError):
      return None
    if op == "direct":
      out = self.direct(req.get("text"))
    elif op == "direct_set":
      if not onroad and (not offroad or moving):
        out = {"ok": False, "error": "차량 상태를 확인할 수 없습니다. 연결 상태를 확인하세요."}
      elif type(req.get("value")) is not int or not isinstance(req.get("name"), str):
        out = {"ok": False, "error": "정수 값을 지정하세요."}
      else:
        out = self.set(req.get("name"), req.get("value"), moving or onroad)
    elif op == "search":
      out = self.search(req.get("keywords"))
    elif op == "get":
      out = self.get(req.get("name"))
    elif op == "set":
      out = self.set(req.get("name"), req.get("value"), moving)
    elif op == "press":
      out = self.press(req.get("cmd"), press)
    elif op in ("system_check", "system", "system_status"):
      if system is None or system_state is None:
        out = {"ok": False, "error": "시스템 작업 경로 없음"}
      elif op == "system_check":
        out = system.check(req.get("action"), system_state())
      elif op == "system":
        out = system.start(req.get("action"), system_state())
      else:
        out = system.status(str(req.get("job", "")))
    else:
      out = {"ok": False, "error": "알 수 없는 요청"}
    out["id"] = rid
    out["moving"] = bool(moving)
    body = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > REPLY_MAX_BYTES and isinstance(out.get("results"), list):
      while out["results"] and len(body) > REPLY_MAX_BYTES:
        out["results"].pop()
        body = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return REPLY_PREFIX + body
