#!/usr/bin/env python3
"""가벼운 가감속 기록기(LongTraceEnabled 가 켜져 있을 때만).

loggerd 없이 앞차 반응 지연을 분석하려고, 레이더 → 계획 → 명령 → 실제 차량
단계의 숫자만 20Hz CSV 로 남긴다(분당 약 100KB). 분석은
selfdrive/debug/long_trace_analyze.py 로 한다.

파일: /data/media/0/long_trace/YYYYmmdd-HHMMSS.csv  (adb 로는 /sdcard/long_trace)
오래된 파일은 MAX_FILES 개 / MAX_TOTAL_BYTES 를 넘으면 지운다.
"""
import os
import time

import cereal.messaging as messaging
from common.params import Params
from common.realtime import Ratekeeper

TRACE_DIR = "/data/media/0/long_trace"
RATE_HZ = 20
PARAM_POLL_S = 1.0
FLUSH_S = 2.0
MAX_FILES = 30
MAX_TOTAL_BYTES = 100 * 1024 * 1024

COLUMNS = [
  # 각 메시지가 만들어진 시각(초, monotonic). 단계별 지연을 잴 때 쓴다.
  "t", "radar_t", "plan_t", "cc_t", "cs_t",
  # ① ② 레이더·앞차 추정
  "lead", "lead_radar", "d_rel", "v_rel", "v_lead", "v_lead_k", "a_lead_k", "a_lead_tau",
  # ④ 계획
  "plan_a_target", "plan_v_target", "plan_a0", "plan_a1s", "mpc_mode", "e2e_reason", "should_stop",
  # ⑤ 명령
  "cmd_accel", "long_state", "enabled",
  # ⑥ 실제 차량
  "v_ego", "a_ego", "gas", "brake",
]


def _rotate(directory):
  try:
    files = sorted((os.path.join(directory, f) for f in os.listdir(directory) if f.endswith(".csv")),
                   key=os.path.getmtime)
  except OSError:
    return
  total = sum(os.path.getsize(f) for f in files)
  while files and (len(files) > MAX_FILES or total > MAX_TOTAL_BYTES):
    oldest = files.pop(0)
    try:
      total -= os.path.getsize(oldest)
      os.remove(oldest)
    except OSError:
      pass


def _f(value, digits=3):
  try:
    return f"{float(value):.{digits}f}"
  except (TypeError, ValueError):
    return ""


def _row(sm):
  lead = sm['radarState'].leadOne
  plan = sm['longitudinalPlan']
  cs = sm['carState']
  ctl = sm['controlsState']
  accels = list(plan.accels)
  mono = sm.logMonoTime
  return [
    _f(time.monotonic()),
    _f(mono['radarState'] * 1e-9), _f(mono['longitudinalPlan'] * 1e-9),
    _f(mono['carControl'] * 1e-9), _f(mono['carState'] * 1e-9),
    str(int(lead.status)), str(int(lead.radar)),
    _f(lead.dRel, 2), _f(lead.vRel), _f(lead.vLead), _f(lead.vLeadK), _f(lead.aLeadK), _f(lead.aLeadTau, 2),
    _f(plan.aTarget), _f(plan.vTargetNow), _f(accels[0] if accels else 0.0),
    # T_IDXS 10번째 근처가 약 1초 뒤(계획이 앞으로 무엇을 하려는지)
    _f(accels[10] if len(accels) > 10 else 0.0),
    str(int(plan.mpcMode)), str(int(plan.e2eReason)), str(int(plan.shouldStop)),
    _f(sm['carControl'].actuators.accel), str(int(ctl.longControlState)), str(int(ctl.enabled)),
    _f(cs.vEgo), _f(cs.aEgo), str(int(cs.gasPressed)), str(int(cs.brakePressed)),
  ]


def main():
  params = Params()
  sm = messaging.SubMaster(['radarState', 'longitudinalPlan', 'carControl', 'carState', 'controlsState'],
                           ignore_avg_freq=['radarState', 'longitudinalPlan', 'carControl', 'carState', 'controlsState'])
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
        enabled = params.get_bool("LongTraceEnabled")
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
