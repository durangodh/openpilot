"""Setting lookup and change requests from the S20 HUD voice assistant (HUDPRM1).

The phone asks over the existing 7210 UDP link; the reply goes straight back to the
sender. Only settings listed in selfdrive/apilot.json (the APM list) can be read or
written, every write is range-checked here, and while the car moves only the cruise and
longitudinal groups can change. Whatever the AI on the phone asks for, these rules hold.

Request : HUDPRM1 {"id": "...", "op": "search"|"get"|"set"|"press"|"memo"|
                                   "system_check"|"system"|"system_status", ...}
          system ops are git pull / reboot (voice_system.py: parked or off only).
          press only accepts cmd "gap" (차간 단계 = 크루즈 GAP 버튼 상태, 설정값 아님).
Reply   : HUDPRM1R {"id": "...", "ok": true|false, ...}
"""
import json
import os
import time

PREFIX = b"HUDPRM1 "
REPLY_PREFIX = b"HUDPRM1R "
APILOT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apilot.json")
# 주행 중 변경 가능한 그룹(가감속·차간·크루즈). 횡방향·화면·기본 설정은 정차 중에만 바꾼다.
DRIVING_GROUPS = ("크루즈", "종방향")
SEARCH_LIMIT = 8
DESCR_MAX = 90
REPLY_MAX_BYTES = 6000
# 음성 메모: trace_http.TRACE_DIRS["memo"] 와 같아야 한다. 하루 한 파일에 한 줄씩 쌓는다.
MEMO_DIR = "/data/media/0/voice_memo"
MEMO_TEXT_MAX = 300


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

  def memo(self, text, settings, snapshot, memo_dir=MEMO_DIR, now=None):
    """Driver's spoken note + the car state at that moment, one line in voice-memo-MMDD.log."""
    text = " ".join(str(text or "").split())[:MEMO_TEXT_MAX]
    if not text:
      return {"ok": False, "error": "메모 내용 없음"}
    values = []
    for name in (settings or [])[:6]:
      d = self.definitions.get(name)
      if d is not None:
        values.append("%s=%s" % (name, self._value(d)))
    parts = [time.strftime("%m-%d %H:%M:%S", time.localtime(now))]
    if snapshot:
      parts.append(snapshot)
    if values:
      parts.append("설정 " + " ".join(values))
    parts.append("메모: " + text)
    line = " | ".join(parts)
    path = os.path.join(memo_dir, "voice-memo-%s.log" % time.strftime("%m%d", time.localtime(now)))
    try:
      os.makedirs(memo_dir, exist_ok=True)
      with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    except OSError as e:
      return {"ok": False, "error": "메모 저장 실패: %s" % e}
    return {"ok": True, "file": os.path.basename(path), "line": line}

  @staticmethod
  def press(command, press):
    """Cruise button for the AI. Only GAP: speed and cancel stay with the driver."""
    if command != "gap":
      return {"ok": False, "error": "AI 는 GAP 버튼만 누를 수 있음"}
    if press is None:
      return {"ok": False, "error": "버튼 경로 없음"}
    result = press(command)
    return {"ok": result == "accepted", "result": result,
            "error": "" if result == "accepted" else "지금은 버튼을 보낼 수 없음(시동·차량 데이터 확인)"}

  def handle(self, data, moving, press=None, snapshot=None, system=None, system_state=None):
    """Raw HUDPRM1 datagram -> reply bytes, or None when it is not ours or malformed."""
    if not data.startswith(PREFIX):
      return None
    try:
      req = json.loads(data[len(PREFIX):].decode("utf-8"))
      rid = str(req.get("id", ""))[:40]
      op = req.get("op")
    except (ValueError, AttributeError, UnicodeDecodeError):
      return None
    if op == "search":
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
    elif op == "memo":
      out = self.memo(req.get("text"), req.get("settings"), snapshot() if snapshot else "")
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
