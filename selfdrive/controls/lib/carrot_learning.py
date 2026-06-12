"""
CarrotPilot Auto-Tuner 포팅판 (원본 commit 9dd5e2c, selfdrive/carrot/carrot_learning.py)

이 포크(구형 openpilot + neokii SCC 구조)에 맞춰 단순화한 운전자 개입 기반 학습기.
설치 위치: selfdrive/controls/lib/carrot_learning.py

  [Phase 1] CruiseMaxVals0~3 : 속도대역별 최대가속 (planner의 A_CRUISE_MAX_VALS 대체)
            트리거: 인게이지 중 gasPressed (설정속도보다 3km/h 이상 낮을 때만)
            발동:  대역당 누적 >= 10초
  [Phase 2] PathOffset : 직진 주행 편차 보정 (이 포크에 이미 존재하는 파라미터, 단위 m)
            트리거: 직진(|조향각|<5도, 오버라이드 없음) 중 조향각 평균 편차
            발동:  샘플 >= 400개 (0.05s 주기 -> 약 20초)
  [Phase 4] TFollowGap1~4 : cruiseGap 단계별 추종거리 (long_mpc의 CRUISE_GAP_V 대체)
            트리거: 선행차 추종 중 gas(좁히기 의도) / brake(넓히기 의도) 개입
            발동:  gas 누적 >= 15초 / brake 누적 >= 10초

저장: Params("CarrotLearningData") JSON
추천: P단 전환 시 Params("CarrotLearningRecommend") 기록 + CarrotLearningPopupReady=1
적용: CarrotLearningAutoApply=1 이면 P단 전환 시 즉시 자동 적용 + History 기록 (최대 50개)
      (이 포크에는 추천 팝업/그래프 UI가 없으므로 AutoApply 사용 또는 SSH로 확인 권장)
초기화: CarrotLearningClear=1 -> 누적 학습 데이터 삭제

원본 대비 제외된 것 (이 포크에 대응 신호/파라미터 없음):
  - Phase 3 JLeadFactor3, Phase 5 DynamicTFollow/TFollowDecelBoost (jLead 신호 없음)
    -> 수동 브레이크 개입은 Phase 4의 '거리 넓히기' 신호로 흡수
  - 토크 조향 파라미터 학습 (LateralTorque* 파라미터가 이 포크에 없음)
  - 주행 중 타이머/정차 팝업 (UI 없음, parking 트리거만 유지)
"""
import json
import datetime
import numpy as np

from common.params import Params
from common.realtime import DT_MDL

_DT = DT_MDL  # longitudinal_planner.update() 호출 주기 (0.05s)

# ── Phase 1 상수: A_CRUISE_MAX_BP [0,10,25,40] m/s 와 동일 대역 (kph 환산) ──
_BP_KPH = [0., 36., 90., 144.]
_NUM_BANDS = len(_BP_KPH)
_ACCEL_KEYS = [f"CruiseMaxVals{i}" for i in range(_NUM_BANDS)]
_ACCEL_DEFAULTS = [180, 120, 80, 60]      # A_CRUISE_MAX_VALS x100
_ACCEL_MAX_LIMITS = [220, 150, 110, 80]   # 대역별 안전 상한 (원본 캡 준용)
_ACCEL_MIN = 50
_GAS_THRESHOLD_SEC = 10.0                 # 추천 발동: 누적 가속 개입 시간
_GAS_RECOMMEND_RATIO = 0.10               # 기본 +10%
_GAS_REDUCE_RATIO = -0.07                 # 과가속 시 -7%
_GAS_REDUCE_THRESHOLD_SEC = 5.0           # 브레이크 개입 누적 기준
_MAX_DELTA = 15                           # 세션당 변동폭 제한 (원본 ±15)

# ── Phase 2 상수: PathOffset (단위 m, float) ──
_STRAIGHT_DEG = 5.0
_LATERAL_MIN_SAMPLES = 400                # 0.05s * 400 = 약 20초 직진
_PATH_OFFSET_DEG_THRESHOLD = 1.5          # 평균 편차 이 이상이면 추천
_PATH_OFFSET_M_PER_DEG = 0.01             # 1도 편차 ≈ 0.01m 보정 (실험값)
_PATH_OFFSET_LIMIT = 0.15                 # ±0.15m 제한

# ── Phase 4 상수: CRUISE_GAP_V x100 ──
_TFOLLOW_KEYS = ["TFollowGap1", "TFollowGap2", "TFollowGap3", "TFollowGap4"]
_TFOLLOW_DEFAULTS = [100, 140, 200, 200]  # CRUISE_GAP_V = [1.0, 1.4, 2.0, 2.0]
_TFOLLOW_GAS_THRESHOLD_SEC = 15.0
_TFOLLOW_BRAKE_THRESHOLD_SEC = 10.0
_TFOLLOW_MIN_V_KPH = 40.0
_TFOLLOW_MAX_LEAD_DREL = 150.0
_TFOLLOW_MIN = 90                         # 0.90초 안전 하한
_TFOLLOW_MAX = 220
_TFOLLOW_NARROW_STEP = 5
_TFOLLOW_WIDEN_STEP = 5


# ── Params 헬퍼 (이 포크 python Params에 get_int/put_int가 없으므로) ──────
def _get_int(params, key, default):
  raw = params.get(key, encoding='utf8')
  try:
    return int(raw) if raw else default
  except (TypeError, ValueError):
    return default


def _get_float(params, key, default):
  raw = params.get(key, encoding='utf8')
  try:
    return float(raw) if raw else default
  except (TypeError, ValueError):
    return default


def _remove(params, key):
  """python 바인딩 버전에 따라 remove/delete 명칭이 달라 안전하게 처리"""
  for name in ("remove", "delete"):
    fn = getattr(params, name, None)
    if fn is not None:
      try:
        fn(key)
        return
      except Exception:
        pass
  params.put(key, "")


# ── planner / long_mpc 에서 학습값을 읽어가는 공용 함수 ───────────────────
def read_learned_accel_vals(params):
  """longitudinal_planner의 최대가속 테이블 대체값 (m/s^2 리스트, 길이 4)"""
  return [float(np.clip(_get_int(params, _ACCEL_KEYS[i], _ACCEL_DEFAULTS[i]),
                        _ACCEL_MIN, _ACCEL_MAX_LIMITS[i])) / 100.0
          for i in range(_NUM_BANDS)]


def read_learned_tfollow(params):
  """long_mpc의 CRUISE_GAP_V 대체값 (초 리스트, 길이 4)"""
  return [float(np.clip(_get_int(params, _TFOLLOW_KEYS[i], _TFOLLOW_DEFAULTS[i]),
                        _TFOLLOW_MIN, _TFOLLOW_MAX)) / 100.0
          for i in range(4)]


def _speed_band(v_ego_kph):
  for i in range(_NUM_BANDS - 1, -1, -1):
    if v_ego_kph >= _BP_KPH[i]:
      return i
  return 0


class CarrotLearner:
  """longitudinal_planner.update()에서 매 프레임(0.05s) 호출."""

  def __init__(self):
    self._params = Params()
    # Phase 1
    self._gas_acc = [0.0] * _NUM_BANDS
    self._gas_dec_acc = [0.0] * _NUM_BANDS
    self._gas_max_accel = [0.0] * _NUM_BANDS
    # Phase 2
    self._steer_acc = 0.0
    self._steer_count = 0
    # Phase 4
    self._tfollow_gas_acc = [0.0] * 4
    self._tfollow_brake_acc = [0.0] * 4
    self._tfollow_min_gap = [999.0] * 4
    self._current_gap = 4
    # 공통
    self._prev_brake = False
    self._prev_gear_park = True
    self._has_driven = False
    self._frame = 0
    self._load()

  # ── 공개 API ─────────────────────────────────────────────────────────
  def is_active(self):
    return self._params.get_bool("CarrotLearningActive")

  def set_current_gap(self, gap):
    """현재 cruiseGap 단계 (1~4). planner에서 매 프레임 전달."""
    self._current_gap = max(1, min(4, int(gap)))

  def update(self, v_ego_kph, gas_pressed, engaged, gear_park,
             steer_deg=0.0, steer_pressed=False, brake_pressed=False,
             lead_drel=0.0, lead_v_kph=0.0, a_ego=0.0, v_cruise_kph=0.0,
             gas_val=0.0, blinker=False):
    if not self.is_active():
      if self._params.get_bool("CarrotLearningPopupReady"):
        self._params.put_bool("CarrotLearningPopupReady", False)
      return

    # UI/SSH로부터 초기화 신호
    if self._params.get_bool("CarrotLearningClear"):
      self._clear()
      self._params.put_bool("CarrotLearningClear", False)

    if engaged:
      self._has_driven = True

    # 학습 제외 조건 (깜빡이 / 극단적 가속)
    exclude = blinker or (a_ego > 2.2) or (gas_val > 0.7)

    # ── Phase 1: 가속 개입 (설정속도 오버라이드 목적 가속은 제외) ──
    if engaged and gas_pressed and v_ego_kph >= 1.0 \
       and v_ego_kph < (v_cruise_kph - 3.0) and not exclude:
      i = _speed_band(v_ego_kph)
      self._gas_acc[i] += _DT
      self._gas_max_accel[i] = max(self._gas_max_accel[i], a_ego)

    # 가속 과다 방지: 선행차 없는데 브레이크를 밟는 패턴 (40km/h 이상에서만)
    if engaged and brake_pressed and v_ego_kph >= 40.0 \
       and (lead_drel == 0.0 or lead_drel > 120.0) and not blinker:
      i = _speed_band(v_ego_kph)
      self._gas_dec_acc[i] += _DT

    # ── Phase 2: 직진 편차 (PathOffset) ──
    if engaged and v_ego_kph >= 20.0 and abs(steer_deg) < _STRAIGHT_DEG \
       and not steer_pressed and not blinker:
      self._steer_acc += steer_deg
      self._steer_count += 1

    # ── Phase 4: 선행차 추종 중 페달 개입 ──
    if engaged and v_ego_kph >= _TFOLLOW_MIN_V_KPH \
       and 0.0 < lead_drel < _TFOLLOW_MAX_LEAD_DREL:
      gi = self._current_gap - 1
      if gas_pressed and not exclude:
        self._tfollow_gas_acc[gi] += _DT
        v_ms = v_ego_kph / 3.6
        if v_ms > 1.0:
          self._tfollow_min_gap[gi] = min(self._tfollow_min_gap[gi], lead_drel / v_ms)
      elif brake_pressed and not blinker:
        self._tfollow_brake_acc[gi] += _DT
    self._prev_brake = brake_pressed

    # 10초마다 주기 저장 (전원 차단 대비)
    self._frame += 1
    if self._frame % 200 == 0:
      self._save()

    # ── 주차(P단) 전환 트리거 ──
    if gear_park and not self._prev_gear_park and self._has_driven:
      self._on_parking()
      self._has_driven = False
    self._prev_gear_park = gear_park

  def apply_recommendations(self):
    """SSH 등에서 수동 호출용: 저장된 추천을 적용."""
    raw = self._params.get("CarrotLearningRecommend", encoding='utf8')
    if not raw:
      return
    try:
      recs = json.loads(raw)
    except Exception:
      return
    self._apply(recs)
    _remove(self._params, "CarrotLearningRecommend")
    self._params.put_bool("CarrotLearningPopupReady", False)

  # ── 내부 메서드 ──────────────────────────────────────────────────────
  def _on_parking(self):
    self._save()
    recs = self._calc_recommendations()
    if not recs:
      return
    self._params.put("CarrotLearningRecommend", json.dumps(recs, ensure_ascii=False))
    self._params.put("CarrotLearningPopupSource", "parking")
    self._params.put_bool("CarrotLearningPopupReady", True)
    if self._params.get_bool("CarrotLearningAutoApply"):
      self._apply(recs)
      _remove(self._params, "CarrotLearningRecommend")
      self._params.put_bool("CarrotLearningPopupReady", False)

  def _calc_recommendations(self):
    # ApplyLat/Long 토글: 미설정 시 기본 True
    raw_lat = self._params.get("CarrotTunerApplyLat", encoding='utf8')
    raw_long = self._params.get("CarrotTunerApplyLong", encoding='utf8')
    apply_lat = True if not raw_lat else raw_lat.strip() == "1"
    apply_long = True if not raw_long else raw_long.strip() == "1"

    result = {
      "가속 (Acceleration)": {},
      "조향 (Steering)": {},
      "거리 (Following Distance)": {},
    }

    # ── Phase 1: CruiseMaxVals ──
    if apply_long:
      for i, sec in enumerate(self._gas_acc):
        key = _ACCEL_KEYS[i]
        cur = _get_int(self._params, key, _ACCEL_DEFAULTS[i])
        max_limit = _ACCEL_MAX_LIMITS[i]
        rec, reason = cur, ""
        if cur > max_limit:
          rec, reason = max_limit, f"exceeds limit ({max_limit})"
        elif sec >= _GAS_THRESHOLD_SEC:
          cur_limit = cur / 100.0
          deficit = self._gas_max_accel[i] - cur_limit
          if deficit > 0.05:
            ratio = float(np.clip(deficit / max(cur_limit, 0.1) * 0.8, 0.05, 0.25))
          else:
            ratio = _GAS_RECOMMEND_RATIO
          rec = min(max_limit, int(cur * (1.0 + ratio)))
          reason = f"gas help ({sec:.0f}s, +{ratio*100:.0f}%)"
        elif self._gas_dec_acc[i] >= _GAS_REDUCE_THRESHOLD_SEC:
          rec = int(np.clip(int(cur * (1.0 + _GAS_REDUCE_RATIO)), _ACCEL_MIN, max_limit))
          reason = f"too aggressive ({self._gas_dec_acc[i]:.0f}s brake)"
        if rec != cur:
          rec = cur + int(np.clip(rec - cur, -_MAX_DELTA, _MAX_DELTA))
        if rec != cur:
          result["가속 (Acceleration)"][key] = {
            "current": cur, "recommended": rec,
            "band_kph": f"{_BP_KPH[i]:.0f}km/h~ ({reason})",
          }

    # ── Phase 2: PathOffset ──
    if apply_lat and self._steer_count >= _LATERAL_MIN_SAMPLES:
      avg_deg = self._steer_acc / self._steer_count
      if abs(avg_deg) >= _PATH_OFFSET_DEG_THRESHOLD:
        cur = _get_float(self._params, "PathOffset", 0.0)
        rec = float(np.clip(cur + avg_deg * _PATH_OFFSET_M_PER_DEG,
                            -_PATH_OFFSET_LIMIT, _PATH_OFFSET_LIMIT))
        if abs(rec - cur) >= 0.005:
          result["조향 (Steering)"]["PathOffset"] = {
            "current": round(cur, 3), "recommended": round(rec, 3),
            "band_kph": f"직진 편차 보정 (avg {avg_deg:.2f}deg)",
            "is_float": True,
          }

    # ── Phase 4: TFollowGap ──
    if apply_long:
      for i in range(4):
        key = _TFOLLOW_KEYS[i]
        cur = _get_int(self._params, key, _TFOLLOW_DEFAULTS[i])
        rec, reason = cur, ""
        if self._tfollow_gas_acc[i] >= _TFOLLOW_GAS_THRESHOLD_SEC:
          target = int(self._tfollow_min_gap[i] * 100)
          diff = cur - target
          step = int(np.clip(diff * 0.5, _TFOLLOW_NARROW_STEP, 25)) if diff > 10 else _TFOLLOW_NARROW_STEP
          rec = max(_TFOLLOW_MIN, cur - step)
          reason = f"too wide ({self._tfollow_gas_acc[i]:.0f}s gas)"
        elif self._tfollow_brake_acc[i] >= _TFOLLOW_BRAKE_THRESHOLD_SEC:
          rec = min(_TFOLLOW_MAX, cur + _TFOLLOW_WIDEN_STEP)
          reason = f"too short ({self._tfollow_brake_acc[i]:.0f}s brake)"
        if rec != cur:
          rec = cur + int(np.clip(rec - cur, -_MAX_DELTA, _MAX_DELTA))
        if rec != cur:
          result["거리 (Following Distance)"][key] = {
            "current": cur, "recommended": rec,
            "band_kph": f"GAP{i+1} >=40km/h ({reason})",
          }

    return {k: v for k, v in result.items() if v}

  def _apply(self, recs):
    applied = {}
    for group, items in recs.items():
      g = {}
      for key, info in items.items():
        if info.get("is_float"):
          self._params.put(key, f"{float(info['recommended']):.3f}")
        else:
          self._params.put(key, str(int(info["recommended"])))
        g[key] = info
      if g:
        applied[group] = g

    if applied:
      now = datetime.datetime.now()
      entry = {
        "id": now.strftime("%Y%m%d%H%M%S%f"),
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "changes": applied,
      }
      hist_raw = self._params.get("CarrotLearningHistory", encoding='utf8')
      hist = []
      if hist_raw:
        try:
          hist = json.loads(hist_raw)
          if not isinstance(hist, list):
            hist = []
        except Exception:
          hist = []
      hist.insert(0, entry)
      self._params.put("CarrotLearningHistory", json.dumps(hist[:50], ensure_ascii=False))

    # 적용 후 누적 데이터 초기화 (새 세션부터 재학습)
    self._reset_counters()
    _remove(self._params, "CarrotLearningData")

  def _reset_counters(self):
    self._gas_acc = [0.0] * _NUM_BANDS
    self._gas_dec_acc = [0.0] * _NUM_BANDS
    self._gas_max_accel = [0.0] * _NUM_BANDS
    self._steer_acc = 0.0
    self._steer_count = 0
    self._tfollow_gas_acc = [0.0] * 4
    self._tfollow_brake_acc = [0.0] * 4
    self._tfollow_min_gap = [999.0] * 4
    self._prev_brake = False

  def _clear(self):
    self._reset_counters()
    _remove(self._params, "CarrotLearningData")
    _remove(self._params, "CarrotLearningRecommend")

  def _load(self):
    raw = self._params.get("CarrotLearningData", encoding='utf8')
    if not raw:
      return
    try:
      d = json.loads(raw)
      ga = d.get("gas_acc", [])
      if len(ga) == _NUM_BANDS:
        self._gas_acc = [float(x) for x in ga]
      gd = d.get("gas_dec_acc", [])
      if len(gd) == _NUM_BANDS:
        self._gas_dec_acc = [float(x) for x in gd]
      gm = d.get("gas_max_accel", [])
      if len(gm) == _NUM_BANDS:
        self._gas_max_accel = [float(x) for x in gm]
      self._steer_acc = float(d.get("steer_acc", 0.0))
      self._steer_count = int(d.get("steer_count", 0))
      tg = d.get("tfollow_gas_acc", [])
      if len(tg) == 4:
        self._tfollow_gas_acc = [float(x) for x in tg]
      tb = d.get("tfollow_brake_acc", [])
      if len(tb) == 4:
        self._tfollow_brake_acc = [float(x) for x in tb]
      tm = d.get("tfollow_min_gap", [])
      if len(tm) == 4:
        self._tfollow_min_gap = [float(x) for x in tm]
    except Exception:
      pass  # 데이터 손상 시 기본값 유지

  def _save(self):
    data = {
      "gas_acc": self._gas_acc,
      "gas_dec_acc": self._gas_dec_acc,
      "gas_max_accel": self._gas_max_accel,
      "steer_acc": self._steer_acc,
      "steer_count": self._steer_count,
      "tfollow_gas_acc": self._tfollow_gas_acc,
      "tfollow_brake_acc": self._tfollow_brake_acc,
      "tfollow_min_gap": self._tfollow_min_gap,
    }
    self._params.put("CarrotLearningData", json.dumps(data))
