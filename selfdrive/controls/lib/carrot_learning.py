"""Steering-only driver-intervention learner.

Learns OffsetTotal, lateral torque factor/friction, steering actuator delay,
and steering ratio. Longitudinal/TR/vision-turn acceleration learning is
intentionally excluded.
"""
import json
import datetime
import numpy as np

from common.params import Params
# ── Phase 2 상수: OffsetTotal (단위 m, float) ──
_STRAIGHT_DEG = 5.0
_LATERAL_MIN_SAMPLES = 400                # 0.05s * 400 = 약 20초 직진
_PATH_OFFSET_DEG_THRESHOLD = 1.5          # 평균 편차 이 이상이면 추천
_PATH_OFFSET_M_PER_DEG = 0.01             # 1도 편차 ≈ 0.01m 보정 (실험값)
_PATH_OFFSET_LIMIT = 0.15                 # ±0.15m 제한

# ── Phase 5: 토크 조향 (nTune) ──
_NTUNE_DIR = "/data/ntune"
_TQ_MIN_V_KPH = 40.0
_TQ_CURVE_DEG = 5.0                        # |조향각| 이 이상이면 커브 구간
_TQ_MIN_CURVE_SAMPLES = 600                # 0.05s * 600 = 약 30초 커브 주행
_TQ_OVERRIDE_HI = 0.30                     # 개입 비율 이 이상이면 보정 추천
_TQ_DIR_DOMINANCE = 1.5                    # 한 방향 개입이 반대의 1.5배 이상일 때만
_LAF_STEP = 0.10                           # latAccelFactor 1회 변화량
_LAF_MIN, _LAF_MAX = 1.0, 4.0

# ── 진동-우선 판정 (commit ffec86e 적응, 원본은 LateralTorqueKpV/KiV 대상) ──────
# 이 포크엔 KpV/KiV(Params 기반 피드백 게인)가 없어 latAccelFactor/friction(nTune)에
# 대신 적용한다. 원본의 '추종오차 부호패턴'은 이 포크에 없으므로(steer_err 미보유),
# 대신 이미 수집 중인 '개입 방향(tq_under=조향력부족 / tq_inner=안쪽쏠림)'의 반전
# 패턴을 진동 신호로 쓴다: 방향이 자주 뒤집히면 = latAccelFactor가 실제로는 맞는데
# 진동으로 잘못 개입되는 것 → 크기 신호(override_ratio)만 보고 계속 밀어붙이면
# (구버전 KpV처럼) 발산한다. 고속 직선 핑퐁(개입 없이 조향각 자체가 흔들림)도
# 원본과 동일 로직으로 별도 검출한다.
_TQ_OSC_MIN = 6                # 커브 개입방향 반전(진동) 판정 최소 누적
_TQ_STEADY_MIN = 15             # 커브 개입방향 유지(정상상태 편향) 판정 최소 누적
_TQ_PINGPONG_MIN_KPH = 70.0     # 고속 직선 핑퐁 검출 최소 속도
_TQ_PINGPONG_DEG = 0.4          # 핑퐁 반전 판정 조향각 데드밴드 (도)
_TQ_PINGPONG_MIN_COUNT = 15     # 직선 핑퐁 판정 최소 반전 횟수
_LAF_OSC_STEP = 0.10            # 진동 감지 시 latAccelFactor 완화(토크↓) 스텝
_FRICTION_SUFFICIENT = 0.12     # friction 이 이 이상이면 'FF(마찰보정) 충분' 판정
_LAF_STEADY_STEP = 0.05         # 정상상태 lag 보조 상향/완화 스텝 (기존 _LAF_STEP의 절반)
# ────────────────────────────────────────────────────────────────────────
_TQ_MIN_STR_SAMPLES = 400                  # 직선 약 20초
_STR_OVERRIDE_HI = 0.35                    # friction 상향 기준
_STR_OVERRIDE_LO = 0.05                    # friction 하향 수렴 기준
_FRICTION_STEP = 0.01
_FRICTION_MIN, _FRICTION_MAX = 0.0, 0.20

# ── SteerActuatorDelay (nTune common.json) ──
_NTUNE_COMMON_FILE = "/data/ntune/common.json"
_SAD_STEP = 0.01                           # 1회 변화량 (초)
_SAD_MIN, _SAD_MAX = 0.0, 0.8              # nTune checkValidCommon 범위와 동일
_SAD_OVERRIDE_HI = 0.40                    # 커브 개입 비율 이 이상이면 딜레이 하향(반응 빠르게)
_SAD_OVERRIDE_LO = 0.08                    # 개입 거의 없으면 소폭 상향 수렴(안정)

# ── steerRatio 학습 (nTune common.json, liveParameters 추정 기반) ─────────────
# steerRatio는 openpilot paramsd가 칼만필터로 라이브 추정(liveParameters.steerRatio)한다.
# 그 추정값을 qualifying 구간에서 '정답'으로 누적해 nTune common.json 의 steerRatio 를
# 보정한다. override 방향 휴리스틱으로 유추하지 않는 이유: steerRatio와 latAccelFactor는
# 효과가 겹쳐(degenerate) 같은 개입신호로 둘 다 학습하면 서로 밀어내며 진동한다. 전용
# 추정기 출력을 쓰는 것이 정확하고 안정적이다.
# (참고: common.json useLiveSteerRatio=1 이면 컨트롤러(controlsd)는 라이브 값을 직접 쓰므로
#  이 고정값은 '동결 백업'(추후 useLiveSteerRatio를 끌 때 사용)으로 의미를 가진다.)
_SR_MIN, _SR_MAX = 10.0, 20.0              # nTune checkValidCommon steerRatio 범위와 동일
_SR_DEFAULT = 16.5
_SR_MIN_V_KPH = 30.0                       # 추정 신뢰 구간 (저속 제외)
_SR_MIN_SAMPLES = 600                      # 약 30초 qualifying 주행
_SR_DEADBAND = 0.1                         # 이 이상 차이날 때만 추천 (미세 변동 억제)
_SR_MAX_DELTA = 0.5                        # 세션당 변동 상한 (급변 방지)

# 추천 키 → 조향 학습 Phase.
_KEY_RESET_PHASE = {
  "OffsetTotal": 2,
  "latAccelFactor": 5,
  "friction": 5,
  "steerActuatorDelay": 5,
  "steerRatio": 5,
}


# ── 토크값 저장소 : nTune JSON → Params (carrot 방식) ────────────────────────
# latcontrol_torque 가 LateralTorqueAccelFactor / LateralTorqueFriction 를
# 0.1초마다 다시 읽는다. 키 이름(latAccelFactor/friction)은 학습기 내부 식별자로
# 그대로 두고, 저장/조회만 Params 로 바꾼다.
_TORQUE_PARAM_KEY = {
  "latAccelFactor": ("LateralTorqueAccelFactor", 1000.0, 2.7),
  "friction":       ("LateralTorqueFriction",    1000.0, 0.08),
}


_COMMON_PARAM_KEY = {
  "steerActuatorDelay": ("SteerActuatorDelay", 100.0, 0.10),
  "steerRatio":         ("CustomSteerRatio",   100.0, 16.50),
}


def _common_read():
  from common.params import Params as _P
  out = {}
  pr = _P()
  for k, (pkey, scale, dflt) in _COMMON_PARAM_KEY.items():
    try:
      v = pr.get(pkey, encoding="utf8")
      out[k] = float(v) / scale if v not in (None, "") else dflt
    except (TypeError, ValueError):
      out[k] = dflt
  return out


def _common_write(key, value):
  from common.params import Params as _P
  if key not in _COMMON_PARAM_KEY:
    return
  pkey, scale, _ = _COMMON_PARAM_KEY[key]
  _P().put(pkey, str(int(round(float(value) * scale))))


def _torque_read():
  from common.params import Params as _P
  out = {}
  pr = _P()
  for k, (pkey, scale, dflt) in _TORQUE_PARAM_KEY.items():
    try:
      v = pr.get(pkey, encoding="utf8")
      out[k] = float(v) / scale if v not in (None, "") else dflt
    except (TypeError, ValueError):
      out[k] = dflt
  return out


def _torque_write(key, value):
  from common.params import Params as _P
  if key not in _TORQUE_PARAM_KEY:
    return
  pkey, scale, _ = _TORQUE_PARAM_KEY[key]
  _P().put(pkey, str(int(round(float(value) * scale))))
  _P().put("LateralTorqueCustom", "1")   # 학습값을 쓰려면 커스텀 모드여야 한다


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


class CarrotLearner:
  """longitudinal_planner.update()에서 매 프레임(0.05s) 호출."""

  def __init__(self):
    self._params = Params()
    # Phase 2
    self._steer_acc = 0.0
    self._steer_count = 0
    # Phase 5 (토크 조향)
    self._tq_curve_samples = 0
    self._tq_curve_overrides = 0
    self._tq_under = 0          # 더 꺾는 개입 (조향력 부족)
    self._tq_inner = 0          # 풀어주는 개입 (안쪽 쏠림)
    self._tq_str_samples = 0
    self._tq_str_overrides = 0
    self._tq_prev_pressed = False
    # 진동-우선 판정용 (commit ffec86e 적응)
    self._tq_osc_count = 0          # 커브 개입방향 반전(진동) 누적
    self._tq_steady_count = 0       # 커브 개입방향 유지(정상상태 편향) 누적
    self._tq_straight_reversal = 0  # 고속 직선 조향각 반전(핑퐁) 누적
    self._prev_tq_dir_sign = 0      # 직전 개입방향 부호 (+1 under / -1 inner)
    self._prev_tq_straight_sign = 0 # 직전 직선 조향각 부호 (핑퐁 검출)
    # steerRatio 학습 (liveParameters 추정 누적, Phase 5 조향 그룹)
    self._sr_sum = 0.0
    self._sr_n = 0
    # 공통
    self._prev_brake = False
    self._prev_gear_park = True
    self._has_driven = False
    self._frame = 0
    self._load()
    self._sanitize_stored_learning()

  # ── 공개 API ─────────────────────────────────────────────────────────
  def is_active(self):
    return self._params.get_bool("CarrotLearningActive")

  def update(self, v_ego_kph, engaged, gear_park,
             steer_deg=0.0, steer_pressed=False, a_ego=0.0,
             gas_val=0.0, blinker=False, steer_torque=0.0,
             steer_deg_corr=None, steer_ratio_live=0.0,
             steer_ratio_valid=False):
    if not self.is_active():
      if self._params.get_bool("CarrotLearningPopupReady"):
        self._params.put_bool("CarrotLearningPopupReady", False)
      return

    # UI/SSH로부터 초기화 신호
    if self._params.get_bool("CarrotLearningClear"):
      self._clear()
      self._params.put_bool("CarrotLearningClear", False)

    # Factory Reset 신호 (commit e06a7dd): UI가 Params 기본값을 이미 기록했으므로
    # 여기서는 onroad 인스턴스의 메모리상 누적 카운터만 재동기화 + 플래그 해제
    if self._params.get_bool("CarrotTunerFactoryReset"):
      self._clear()
      self._params.put_bool("CarrotTunerFactoryReset", False)

    if engaged:
      self._has_driven = True

    raw_lat = self._params.get("CarrotTunerApplyLat", encoding='utf8')
    apply_lat = True if not raw_lat else raw_lat.strip() == "1"

    # 학습 제외 조건 (깜빡이 / 극단적 가속)
    exclude = blinker or (a_ego > 2.2) or (gas_val > 0.7)

    # ── Phase 2: 직진 편차 (OffsetTotal) ──
    # 센서 영점 오프셋(angleOffsetDeg) 오학습 방지:
    # controlsState.angleSteers(보정값)가 전달되면 그것을 누적, 없으면 raw 사용
    if apply_lat and engaged and v_ego_kph >= 20.0 and abs(steer_deg) < _STRAIGHT_DEG \
       and not steer_pressed and not blinker:
      dev_deg = steer_deg_corr if steer_deg_corr is not None else steer_deg
      self._steer_acc += dev_deg
      self._steer_count += 1

    # ── Phase 5: 토크 조향 파라미터 (nTune) ──
    if apply_lat and engaged and not blinker:
      if v_ego_kph >= _TQ_MIN_V_KPH and abs(steer_deg) >= _TQ_CURVE_DEG:
        # 커브 구간: 개입 비율 + 방향(운전자 토크 부호) 수집
        self._tq_curve_samples += 1
        if steer_pressed:
          self._tq_curve_overrides += 1
          if not self._tq_prev_pressed:  # 개입 이벤트당 1회만 방향 판정
            dir_sign = 0
            if steer_torque * steer_deg > 0:
              self._tq_under += 1        # 같은 방향으로 더 꺾음 → 조향력 부족
              dir_sign = 1
            elif steer_torque * steer_deg < 0:
              self._tq_inner += 1        # 반대 방향으로 풀어줌 → 안쪽 쏠림
              dir_sign = -1
            # ── 진동-우선 판정 (commit ffec86e 적응) ──────────────────────
            # 개입 방향의 '크기'가 아니라 '부호 반전 패턴'을 본다.
            # 방향 반전(zero-crossing) = 진동(latAccelFactor가 실제로는
            # 맞는데 진동으로 잘못 개입됨) / 방향 유지 = 정상상태 편향(실제 부족/과다).
            # (구버전은 override_ratio(크기)만 보고 계속 밀어붙여 발산 위험)
            if dir_sign != 0:
              if self._prev_tq_dir_sign != 0:
                if dir_sign != self._prev_tq_dir_sign:
                  self._tq_osc_count += 1     # 방향 반전 → 진동
                else:
                  self._tq_steady_count += 1  # 방향 유지 → 정상상태 편향
              self._prev_tq_dir_sign = dir_sign
      elif v_ego_kph >= 30.0 and abs(steer_deg) < _TQ_CURVE_DEG:
        # 완만/직선 구간: friction 학습용 미세 개입 비율
        self._tq_str_samples += 1
        self._prev_tq_dir_sign = 0  # 커브 종료 → 진동 부호 추적 초기화(구간 간 오검출 방지)
        if steer_pressed:
          self._tq_str_overrides += 1
        # 고속 직선 핑퐁(latAccelFactor 과다 개입) 검출: 운전자 개입 없이
        # 조향각이 0 근처에서 반복 반전 → 순수 토크 과다 신호 (commit ffec86e).
        elif v_ego_kph >= _TQ_PINGPONG_MIN_KPH:
          s_sign = 0
          if steer_deg > _TQ_PINGPONG_DEG:
            s_sign = 1
          elif steer_deg < -_TQ_PINGPONG_DEG:
            s_sign = -1
          if s_sign != 0:
            if self._prev_tq_straight_sign != 0 and s_sign != self._prev_tq_straight_sign:
              self._tq_straight_reversal += 1
            self._prev_tq_straight_sign = s_sign
    self._tq_prev_pressed = steer_pressed

    # ── steerRatio 수집: liveParameters(paramsd 칼만 추정)을 qualifying 구간에서 누적 ──
    # 전용 추정기 출력을 '정답'으로 모은다. (override 휴리스틱으로 유추하면 latAccelFactor와
    #  효과가 겹쳐 서로 밀어내며 진동하므로 사용하지 않음). 인게이지·일정속도·유효추정·
    #  비깜빡이 구간만 신뢰.
    if apply_lat and engaged and not blinker and steer_ratio_valid \
       and v_ego_kph >= _SR_MIN_V_KPH and _SR_MIN <= steer_ratio_live <= _SR_MAX:
      self._sr_sum += steer_ratio_live
      self._sr_n += 1

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
    raw_lat = self._params.get("CarrotTunerApplyLat", encoding='utf8')
    apply_lat = True if not raw_lat else raw_lat.strip() == "1"

    result = {"조향 (Steering)": {}}

    # ── Phase 2: OffsetTotal ──
    if apply_lat and self._steer_count >= _LATERAL_MIN_SAMPLES:
      avg_deg = self._steer_acc / self._steer_count
      if abs(avg_deg) >= _PATH_OFFSET_DEG_THRESHOLD:
        cur = _get_float(self._params, "OffsetTotal", 0.0)
        rec = float(np.clip(cur + avg_deg * _PATH_OFFSET_M_PER_DEG,
                            -_PATH_OFFSET_LIMIT, _PATH_OFFSET_LIMIT))
        if abs(rec - cur) >= 0.005:
          result["조향 (Steering)"]["OffsetTotal"] = {
            "current": round(cur, 3), "recommended": round(rec, 3),
            "band_kph": f"직진 편차 보정 (avg {avg_deg:.2f}deg)",
            "is_float": True,
          }

    # ── Phase 5: latAccelFactor / friction (nTune, 토크 제어 차량만) ──
    if apply_lat and self._is_torque_control():
      tq = _torque_read()

      # (a) latAccelFactor + friction 동시 조정 (commit e06a7dd Phase 0)
      #     latAccelFactor는 분모이므로 값↓ = 토크↑.
      #     언더스티어(조향력 부족) → factor↓ + friction↑ (응답 보강)
      #     안쪽 쏠림            → factor↑ + friction↓ (응답 완화)
      #
      #     ── 진동-우선 판정 (commit ffec86e 적응) ──────────────────────
      #     핵심 개선: 개입 '비율(크기)'만 보고 방향으로 계속 밀어붙이면, 진동이
      #     만든 개입도 '방향성 편향'으로 오인해 latAccelFactor를 계속 같은
      #     방향으로 밀어 발산할 수 있다(원본 KpV 무한상향 버그와 동일 패턴).
      #     개입 방향의 부호가 자주 반전(진동)되거나 고속 직선에서 조향각 자체가
      #     핑퐁이면, 방향성 판단(언더/이너) 대신 토크를 완화하는 쪽으로만 조정한다.
      cur_fr_curve = float(tq.get("friction", 0.08))
      fr_from_curve = None
      curve_osc_dominant = (self._tq_osc_count >= _TQ_OSC_MIN
                            and self._tq_osc_count > self._tq_steady_count)
      straight_pingpong = self._tq_straight_reversal >= _TQ_PINGPONG_MIN_COUNT
      if straight_pingpong or curve_osc_dominant:
        cur_laf = float(tq.get("latAccelFactor", 2.7))
        rec_laf = min(_LAF_MAX, round(cur_laf + _LAF_OSC_STEP, 3))
        fr_from_curve = max(_FRICTION_MIN, round(cur_fr_curve - _FRICTION_STEP, 3))
        reason = (f"고속 핑퐁/진동 감지 → 조향력 완화 "
                 f"(방향반전 {self._tq_osc_count}회, 직선반전 {self._tq_straight_reversal}회)")
        if rec_laf != cur_laf:
          result["조향 (Steering)"]["latAccelFactor"] = {
            "current": round(cur_laf, 3), "recommended": rec_laf,
            "band_kph": reason, "is_float": True, "ntune": "torque",
          }
      elif (self._tq_steady_count >= _TQ_STEADY_MIN and self._tq_osc_count < _TQ_OSC_MIN
            and self._tq_curve_samples >= _TQ_MIN_CURVE_SAMPLES
            and (self._tq_curve_overrides / self._tq_curve_samples) < _TQ_OVERRIDE_HI):
        # 정상상태 lag(방향 반전 없이 한쪽으로 꾸준히 개입): 원본은 이 경우 FF(Kf)
        # 강화를 우선시하고, FF가 이미 충분할 때만 Kp를 보조로 소폭 상향한다.
        # 이 포크의 FF 대응 파라미터는 friction 이므로, friction이 이미 '충분'
        # (_FRICTION_SUFFICIENT 이상)할 때만 latAccelFactor를 작은 스텝으로 보조
        # 조정한다. friction이 아직 부족하면 아래 비율 기반(≥30%) 분기가 언더/이너
        # 판정으로 latAccelFactor+friction을 함께 채우도록 그대로 둔다(우선순위 양보).
        if cur_fr_curve >= _FRICTION_SUFFICIENT and self._tq_under != self._tq_inner:
          cur_laf = float(tq.get("latAccelFactor", 2.7))
          if self._tq_under > self._tq_inner:
            rec_laf = max(_LAF_MIN, round(cur_laf - _LAF_STEADY_STEP, 3))
            reason = f"정상상태 조향력 부족 지속 (FF 충분, 보조 상향 {self._tq_under}회)"
          else:
            rec_laf = min(_LAF_MAX, round(cur_laf + _LAF_STEADY_STEP, 3))
            reason = f"정상상태 안쪽쏠림 지속 (FF 충분, 보조 완화 {self._tq_inner}회)"
          if rec_laf != cur_laf:
            result["조향 (Steering)"]["latAccelFactor"] = {
              "current": round(cur_laf, 3), "recommended": rec_laf,
              "band_kph": reason, "is_float": True, "ntune": "torque",
            }
      elif self._tq_curve_samples >= _TQ_MIN_CURVE_SAMPLES and self._tq_curve_overrides > 0:
        ratio = self._tq_curve_overrides / self._tq_curve_samples
        cur_laf = float(tq.get("latAccelFactor", 2.7))
        rec_laf, reason = cur_laf, ""
        if ratio >= _TQ_OVERRIDE_HI:
          if self._tq_under >= self._tq_inner * _TQ_DIR_DOMINANCE:
            # 낮을수록 같은 횡가속에 더 큰 토크 → 조향 강화
            rec_laf = max(_LAF_MIN, round(cur_laf - _LAF_STEP, 3))
            fr_from_curve = min(_FRICTION_MAX, round(cur_fr_curve + _FRICTION_STEP, 3))
            reason = f"커브 조향력 부족 (개입 {ratio*100:.0f}%, 더꺾음 {self._tq_under}회)"
          elif self._tq_inner >= self._tq_under * _TQ_DIR_DOMINANCE:
            rec_laf = min(_LAF_MAX, round(cur_laf + _LAF_STEP, 3))
            fr_from_curve = max(_FRICTION_MIN, round(cur_fr_curve - _FRICTION_STEP, 3))
            reason = f"커브 안쪽 쏠림 (개입 {ratio*100:.0f}%, 풀어줌 {self._tq_inner}회)"
        if rec_laf != cur_laf:
          result["조향 (Steering)"]["latAccelFactor"] = {
            "current": round(cur_laf, 3), "recommended": rec_laf,
            "band_kph": reason, "is_float": True, "ntune": "torque",
          }

      # (b) friction: 커브 동조정이 있으면 우선, 없으면 직선 미세 개입으로 학습
      if fr_from_curve is not None and fr_from_curve != cur_fr_curve:
        result["조향 (Steering)"]["friction"] = {
          "current": round(cur_fr_curve, 3), "recommended": fr_from_curve,
          "band_kph": "커브 방향 보정 동조정 (latAccelFactor 연동)",
          "is_float": True, "ntune": "torque",
        }
      elif fr_from_curve is None and self._tq_str_samples >= _TQ_MIN_STR_SAMPLES:
        r = self._tq_str_overrides / self._tq_str_samples
        cur_fr = float(tq.get("friction", 0.08))
        rec_fr, reason = cur_fr, ""
        if r >= _STR_OVERRIDE_HI:
          rec_fr = min(_FRICTION_MAX, round(cur_fr + _FRICTION_STEP, 3))
          reason = f"직선 미세 불감대 해소 (개입 {r*100:.0f}%)"
        elif r < _STR_OVERRIDE_LO and cur_fr > 0.02:
          rec_fr = max(_FRICTION_MIN, round(cur_fr - 0.005, 3))
          reason = "마찰보상 안정화 감쇄"
        if rec_fr != cur_fr:
          result["조향 (Steering)"]["friction"] = {
            "current": round(cur_fr, 3), "recommended": rec_fr,
            "band_kph": reason, "is_float": True, "ntune": "torque",
          }

      # (c) steerActuatorDelay (nTune common.json): 커브 개입 비율 기반
      # 개입이 잦음 = 조향 타이밍이 안 맞음 → 딜레이 낮춰 더 빠르게 반응
      if self._tq_curve_samples >= _TQ_MIN_CURVE_SAMPLES:
        ratio = self._tq_curve_overrides / max(self._tq_curve_samples, 1)
        common = _common_read()
        cur_sad = float(common.get("steerActuatorDelay", 0.1))
        rec_sad, reason = cur_sad, ""
        if ratio >= _SAD_OVERRIDE_HI:
          rec_sad = max(_SAD_MIN, round(cur_sad - _SAD_STEP, 3))
          reason = f"커브 조향 지연 (개입 {ratio*100:.0f}% → 반응 빠르게)"
        elif ratio < _SAD_OVERRIDE_LO:
          rec_sad = min(_SAD_MAX, round(cur_sad + _SAD_STEP, 3))
          reason = f"커브 안정 (개입 {ratio*100:.0f}% → 지연 소폭 상향)"
        if rec_sad != cur_sad:
          result["조향 (Steering)"]["steerActuatorDelay"] = {
            "current": round(cur_sad, 3), "recommended": rec_sad,
            "band_kph": reason, "is_float": True, "ntune": "common",
          }

    # ── steerRatio (nTune common.json): liveParameters 추정 평균으로 보정 ──
    # common.json은 제어방식(torque/indi) 무관이라 torque 게이트 밖에서 처리한다.
    # 전용 추정기(paramsd) 출력의 평균을 nTune 고정값으로 굳힌다. useLiveSteerRatio=1
    # 이면 컨트롤러는 라이브 값을 쓰므로 이 고정값은 '동결 백업'으로 의미.
    if apply_lat and self._sr_n >= _SR_MIN_SAMPLES:
      mean_sr = self._sr_sum / self._sr_n
      common = _common_read()
      cur_sr = float(common.get("steerRatio", _SR_DEFAULT))
      if abs(mean_sr - cur_sr) >= _SR_DEADBAND:
        # 세션당 변동 상한으로 급변 방지 후 nTune 유효범위 클램프
        rec_sr = cur_sr + float(np.clip(mean_sr - cur_sr, -_SR_MAX_DELTA, _SR_MAX_DELTA))
        rec_sr = round(float(np.clip(rec_sr, _SR_MIN, _SR_MAX)), 2)
        if abs(rec_sr - cur_sr) >= 0.01:
          result["조향 (Steering)"]["steerRatio"] = {
            "current": round(cur_sr, 2), "recommended": rec_sr,
            "band_kph": f"라이브 추정 평균 {mean_sr:.2f} (n={self._sr_n}) → steerRatio 보정",
            "is_float": True, "ntune": "common",
          }

    return {k: v for k, v in result.items() if v}

  def _is_torque_control(self):
    """이 포크는 LateralControl 파라미터가 비어있거나 TORQUE일 때 토크 제어 (CommunityPanel 기본값)"""
    raw = self._params.get("LateralControl", encoding='utf8')
    return (not raw) or raw.strip().upper() == "TORQUE"

  def _apply(self, recs):
    applied = {}
    for group, items in recs.items():
      g = {}
      for key, info in items.items():
        if key not in _KEY_RESET_PHASE:
          continue
        if info.get("ntune") == "torque":
          # Params 직접 수정 → latcontrol_torque 가 0.1초마다 라이브 반영
          _torque_write(key, float(info["recommended"]))
        elif info.get("ntune") == "common":
          # Params 직접 수정 → live_tune 이 1초 주기로 라이브 반영
          # (steerActuatorDelay / steerRatio 모두 이 경로)
          _common_write(key, float(info["recommended"]))
        elif info.get("is_float"):
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

    # ── 적용된 Phase의 누적치만 선택적으로 리셋 ─────────────────────────
    # 적용되지 않은 Phase(특히 느리게 쌓이는 조향 Phase2/5)는 데이터를 보존해
    # 무관한 longitudinal 적용 때문에 학습 진척이 초기화되지 않도록 한다.
    applied_phases = set()
    for group in applied:
      for key in applied[group]:
        ph = _KEY_RESET_PHASE.get(key)
        if ph is not None:
          applied_phases.add(ph)
    phase_reset = {
      2: self._reset_phase2,
      5: self._reset_phase5,
    }
    for ph in applied_phases:
      phase_reset[ph]()

    # 보존된 Phase 데이터가 재부팅에도 살아남도록 즉시 저장(remove 대신 _save).
    self._save()

  def _reset_phase2(self):
    """직진 편차 (OffsetTotal)"""
    self._steer_acc = 0.0
    self._steer_count = 0

  def _reset_phase5(self):
    """조향 (latAccelFactor/friction/steerActuatorDelay/steerRatio) — 방향·추정 카운터 포함"""
    self._tq_curve_samples = 0
    self._tq_curve_overrides = 0
    self._tq_under = 0
    self._tq_inner = 0
    self._tq_str_samples = 0
    self._tq_str_overrides = 0
    self._tq_prev_pressed = False
    # 진동/핑퐁 카운터도 함께 리셋 (commit ffec86e 적응)
    self._tq_osc_count = 0
    self._tq_steady_count = 0
    self._tq_straight_reversal = 0
    self._prev_tq_dir_sign = 0
    self._prev_tq_straight_sign = 0
    # steerRatio 누적도 함께 리셋 (Phase 5 조향 그룹 귀속)
    self._sr_sum = 0.0
    self._sr_n = 0

  def _reset_all_phases(self):
    self._reset_phase2()
    self._reset_phase5()

  @staticmethod
  def _steering_only(recs):
    if not isinstance(recs, dict):
      return {}
    filtered = {}
    for group, items in recs.items():
      if not isinstance(items, dict):
        continue
      steering_items = {key: info for key, info in items.items() if key in _KEY_RESET_PHASE}
      if steering_items:
        filtered[group] = steering_items
    return filtered

  def _sanitize_stored_learning(self):
    raw = self._params.get("CarrotLearningRecommend", encoding='utf8')
    if raw:
      try:
        recs = self._steering_only(json.loads(raw))
        if recs:
          self._params.put("CarrotLearningRecommend", json.dumps(recs, ensure_ascii=False))
        else:
          _remove(self._params, "CarrotLearningRecommend")
          self._params.put_bool("CarrotLearningPopupReady", False)
      except Exception:
        _remove(self._params, "CarrotLearningRecommend")
        self._params.put_bool("CarrotLearningPopupReady", False)

    raw = self._params.get("CarrotLearningHistory", encoding='utf8')
    if raw:
      try:
        history = json.loads(raw)
        filtered_history = []
        for entry in history if isinstance(history, list) else []:
          if not isinstance(entry, dict):
            continue
          changes = self._steering_only(entry.get("changes", {}))
          if changes:
            clean_entry = dict(entry)
            clean_entry["changes"] = changes
            filtered_history.append(clean_entry)
        if filtered_history:
          self._params.put("CarrotLearningHistory", json.dumps(filtered_history[:50], ensure_ascii=False))
        else:
          _remove(self._params, "CarrotLearningHistory")
      except Exception:
        _remove(self._params, "CarrotLearningHistory")

    # Rewrite accumulated data immediately, dropping legacy longitudinal sections.
    self._save()

  def _clear(self):
    self._reset_all_phases()
    _remove(self._params, "CarrotLearningData")
    _remove(self._params, "CarrotLearningRecommend")

  def _load(self):
    raw = self._params.get("CarrotLearningData", encoding='utf8')
    if not raw:
      return
    try:
      d = json.loads(raw)
      self._steer_acc = float(d.get("steer_acc", 0.0))
      self._steer_count = int(d.get("steer_count", 0))
      tq = d.get("tq", {})
      self._tq_curve_samples = int(tq.get("curve_samples", 0))
      self._tq_curve_overrides = int(tq.get("curve_overrides", 0))
      self._tq_under = int(tq.get("under", 0))
      self._tq_inner = int(tq.get("inner", 0))
      self._tq_str_samples = int(tq.get("str_samples", 0))
      self._tq_str_overrides = int(tq.get("str_overrides", 0))
      self._tq_osc_count = int(tq.get("osc_count", 0))
      self._tq_steady_count = int(tq.get("steady_count", 0))
      self._tq_straight_reversal = int(tq.get("straight_reversal", 0))
      self._sr_sum = float(tq.get("sr_sum", 0.0))
      self._sr_n = int(tq.get("sr_n", 0))
    except Exception:
      pass  # 데이터 손상 시 기본값 유지

  def _save(self):
    data = {
      "steer_acc": self._steer_acc,
      "steer_count": self._steer_count,
      "tq": {
        "curve_samples": self._tq_curve_samples,
        "curve_overrides": self._tq_curve_overrides,
        "under": self._tq_under,
        "inner": self._tq_inner,
        "str_samples": self._tq_str_samples,
        "str_overrides": self._tq_str_overrides,
        "osc_count": self._tq_osc_count,
        "steady_count": self._tq_steady_count,
        "straight_reversal": self._tq_straight_reversal,
        "sr_sum": self._sr_sum,
        "sr_n": self._sr_n,
      },
    }
    self._params.put("CarrotLearningData", json.dumps(data))
