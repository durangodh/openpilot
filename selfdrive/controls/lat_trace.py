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
from selfdrive.controls.long_trace import FLUSH_S, PARAM_POLL_S, RATE_HZ, _f, _rotate

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
]


def _row(sm):
  plan = sm['lateralPlan']
  ctl = sm['controlsState']
  cc = sm['carControl']
  cs = sm['carState']
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
    _f(cs.vEgo), str(int(cc.latActive)), str(int(ctl.enabled)), str(int(cs.steeringPressed)),
    str(int(plan.laneChangeState)), str(int(plan.desire)),
    _f(curvs[0] if curvs else 0.0, 5), _f(ctl.desiredCurvature, 5), _f(ctl.desiredCurvatureRate, 5),
    (str(int(torque.active)) if torque is not None else ""),
    tq("desiredLateralAccel"), tq("actualLateralAccel"), tq("error"), tq("p"), tq("i"), tq("f"),
    tq("output"), (str(int(torque.saturated)) if torque is not None else ""),
    _f(cc.actuators.steer, 4), _f(cc.actuatorsOutput.steer, 4),
    _f(ctl.curvature, 5), _f(cs.steeringAngleDeg, 2), _f(cs.steeringRateDeg, 2), _f(cs.yawRate, 4),
    _f(cs.steeringTorque, 1), _f(cs.steeringTorqueEps, 1),
    _f(ctl.steerRatio, 2), _f(ctl.steerActuatorDelay, 3),
  ]


def main():
  params = Params()
  services = ['lateralPlan', 'controlsState', 'carControl', 'carState']
  sm = messaging.SubMaster(services, ignore_avg_freq=services)
  rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)
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
        if not enabled and out is not None:
          out.close()
          out = None

      if not enabled:
        time.sleep(PARAM_POLL_S)
        continue

      sm.update(0)
      if out is None:
        os.makedirs(TRACE_DIR, exist_ok=True)
        _rotate(TRACE_DIR)
        path = os.path.join(TRACE_DIR, time.strftime("%Y%m%d-%H%M%S") + ".csv")
        out = open(path, "w", buffering=1 << 16)
        out.write(",".join(COLUMNS) + "\n")

      if sm.all_alive(['carState']):
        out.write(",".join(_row(sm)) + "\n")
      if now >= next_flush:
        next_flush = now + FLUSH_S
        out.flush()
      rk.keep_time()
  finally:
    if out is not None:
      out.close()


if __name__ == "__main__":
  main()
