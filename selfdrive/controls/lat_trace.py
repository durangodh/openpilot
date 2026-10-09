#!/usr/bin/env python3
"""가벼운 조향 기록기(LatTraceEnabled 가 켜져 있을 때만).

loggerd 없이 조향 반응 지연·추종 오차·흔들림을 분석하려고, 계획 → 제어 → 명령 →
실제 차량 단계의 숫자만 20Hz CSV 로 남긴다(분당 약 150KB). 분석은
selfdrive/debug/lat_trace_analyze.py 로 한다.

파일: /data/media/0/lat_trace/YYYYmmdd-HHMMSS.csv  (adb 로는 /sdcard/lat_trace)
오래된 파일은 30개 / 100MB 를 넘으면 지운다(long_trace 와 같은 규칙).
"""
import os
import time

import cereal.messaging as messaging
from common.params import Params
from common.realtime import Ratekeeper, sec_since_boot
from selfdrive.controls.long_trace import ERROR_RETRY_S, FLUSH_S, PARAM_POLL_S, RATE_HZ, _close, _f, _i, _new_path, _rotate, _safe_row

TRACE_DIR = "/data/media/0/lat_trace"

COLUMNS = [
  # 각 메시지가 만들어진 시각(초). 단계별 지연을 잴 때 쓴다.
  "t", "plan_t", "ctl_t", "cc_t", "cs_t",
  # 상태
  "v_ego", "lat_active", "enabled", "steer_pressed", "lane_change", "desire",
  # 계획(경로가 원하는 곡률)
  "plan_curv0", "desired_curv", "desired_curv_rate",
  # 토크 제어기 내부(목표/실제 횡가속, 오차, P/I/F, 출력)
  "t_active", "t_desired_lat_accel", "t_actual_lat_accel", "t_error", "t_p", "t_i", "t_f",
  "t_output", "t_saturated",
  # 명령
  "cmd_steer", "cmd_steer_out",
  # 실제 차량
  "curv_actual", "angle_deg", "rate_deg", "yaw_rate", "steer_torque", "steer_torque_eps",
  # 사용 중인 조향 상수
  "steer_ratio", "steer_delay",
  # 학습된 핸들 영점·도로 기울기·조향비(liveParameters)와 커브 적분 기준
  "angle_offset", "roll", "live_sr",
  # LatYawMeasureBlend 가 섞는 EON 자이로 요레이트(liveLocationKalman, rad/s, +=왼쪽)
  "yaw_llk", "yaw_llk_valid",
  # 경로 좌우 위치(m, +=왼쪽): 모델 원본(modelV2.position)과 차선/레인리스 혼합 후 경로
  # (dPathWLines, 사용자 오프셋 전)의 20 m·40 m 지점, 차선 신뢰도, 레인리스 여부
  "model_y20", "model_y40", "path_y20", "path_y40", "l_prob", "r_prob", "laneless",
]


def _y_at(xs, ys, dist):
  try:
    xs, ys = list(xs), list(ys)
    if len(xs) < 2 or len(xs) != len(ys) or xs[-1] < dist:
      return ""
    for i in range(1, len(xs)):
      if xs[i] >= dist:
        k = (dist - xs[i - 1]) / max(xs[i] - xs[i - 1], 1e-6)
        return _f(ys[i - 1] + k * (ys[i] - ys[i - 1]), 3)
  except Exception:
    pass
  return ""


def _row(sm):
  plan = sm['lateralPlan']
  ctl = sm['controlsState']
  cc = sm['carControl']
  cs = sm['carState']
  lp = sm['liveParameters']
  md = sm['modelV2']
  yaw_llk, yaw_llk_valid = "", _i(0)
  try:
    av = sm['liveLocationKalman'].angularVelocityCalibrated
    if len(av.value) > 2:
      yaw_llk, yaw_llk_valid = _f(av.value[2], 5), _i(av.valid)
  except Exception:
    pass
  curvs = list(plan.curvatures)
  torque = None
  try:
    if ctl.lateralControlState.which() == 'torqueState':
      torque = ctl.lateralControlState.torqueState
  except Exception:
    torque = None
  mono = sm.logMonoTime

  def tq(name, digits=4):
    return _f(getattr(torque, name), digits) if torque is not None else ""

  return [
    _f(sec_since_boot()),   # logMonoTime 과 같은 시계
    _f(mono['lateralPlan'] * 1e-9), _f(mono['controlsState'] * 1e-9),
    _f(mono['carControl'] * 1e-9), _f(mono['carState'] * 1e-9),
    _f(cs.vEgo), _i(cc.latActive), _i(ctl.enabled), _i(cs.steeringPressed),
    _i(plan.laneChangeState), _i(plan.desire),
    _f(curvs[0] if curvs else 0.0, 5), _f(ctl.desiredCurvature, 5), _f(ctl.desiredCurvatureRate, 5),
    (_i(torque.active) if torque is not None else ""),
    tq("desiredLateralAccel"), tq("actualLateralAccel"), tq("error"), tq("p"), tq("i"), tq("f"),
    tq("output"), (_i(torque.saturated) if torque is not None else ""),
    _f(cc.actuators.steer, 4), _f(cc.actuatorsOutput.steer, 4),
    _f(ctl.curvature, 5), _f(cs.steeringAngleDeg, 2), _f(cs.steeringRateDeg, 2), _f(cs.yawRate, 4),
    _f(cs.steeringTorque, 1), _f(cs.steeringTorqueEps, 1),
    _f(ctl.steerRatio, 2), _f(ctl.steerActuatorDelay, 3),
    _f(lp.angleOffsetDeg, 3), _f(lp.roll, 4), _f(lp.steerRatio, 2),
    yaw_llk, yaw_llk_valid,
    _y_at(md.position.x, md.position.y, 20.0), _y_at(md.position.x, md.position.y, 40.0),
    _y_at(plan.dPathWLinesX, plan.dPathWLinesY, 20.0), _y_at(plan.dPathWLinesX, plan.dPathWLinesY, 40.0),
    _f(plan.lProb, 2), _f(plan.rProb, 2), _i(not plan.useLaneLines),
  ]


def main():
  params = Params()
  services = ['lateralPlan', 'controlsState', 'carControl', 'carState', 'liveParameters', 'liveLocationKalman',
              'modelV2']
  # 소켓은 기록할 때만 연다. 꺼진 채 열어 두면 읽지 않아 "Reader was evicted" 가 난다.
  sm = None
  rk = None
  enabled = False
  next_param = 0.0
  next_flush = 0.0
  out = None
  try:
    while True:
      now = time.monotonic()
      if now >= next_param:
        next_param = now + PARAM_POLL_S
        enabled = params.get_bool("LatTraceEnabled")
        if not enabled:
          out = _close(out)
          sm = None

      if not enabled:
        time.sleep(PARAM_POLL_S)
        continue

      if sm is None:
        sm = messaging.SubMaster(services, ignore_avg_freq=services)
        # 켤 때마다 새로 만든다. 꺼져 있던 동안 밀린 주기를 따라잡느라 쉬지 않고
        # 같은 줄을 몰아 쓰는 일이 없게 한다.
        rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)
      sm.update(0)
      try:
        if out is None:
          os.makedirs(TRACE_DIR, exist_ok=True)
          _rotate(TRACE_DIR)
          out = open(_new_path(TRACE_DIR), "w", buffering=1 << 16)
          out.write(",".join(COLUMNS) + "\n")
        if sm.all_alive(['carState']):
          row = _safe_row(_row, sm)
          if row is not None:
            out.write(row)
        if now >= next_flush:
          next_flush = now + FLUSH_S
          out.flush()
      except OSError:
        # 저장 공간 부족 등: 죽지 않고 파일을 닫은 뒤 잠시 쉬었다 새 파일로 다시 시도한다.
        out = _close(out)
        sm = None   # 다시 켤 때처럼 소켓·주기를 새로 시작한다(쉰 동안 밀린 주기 몰아 쓰기 방지)
        time.sleep(ERROR_RETRY_S)
        continue
      rk.keep_time()
  finally:
    _close(out)


if __name__ == "__main__":
  main()
