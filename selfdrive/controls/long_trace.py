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
from common.realtime import Ratekeeper, sec_since_boot

TRACE_DIR = "/data/media/0/long_trace"
RATE_HZ = 20
PARAM_POLL_S = 1.0
FLUSH_S = 2.0
MAX_FILES = 30
MAX_TOTAL_BYTES = 100 * 1024 * 1024
ERROR_RETRY_S = 5.0

COLUMNS = [
  # 각 메시지가 만들어진 시각(초, 부팅 후). 단계별 지연을 잴 때 쓴다.
  "t", "radar_t", "plan_t", "cc_t", "cs_t",
  # ① ② 레이더·앞차 추정
  "lead", "lead_radar", "d_rel", "v_rel", "v_lead", "v_lead_k", "a_lead_k", "a_lead_tau",
  # ④ 계획
  "plan_a_target", "plan_v_target", "plan_a0", "plan_a1s", "mpc_mode", "e2e_reason", "should_stop",
  # 정지차 일정 감속 목표(m/s², 0 이면 MPC 계획 그대로)
  "const_stop",
  # ⑤ 명령
  "cmd_accel", "long_state", "enabled", "long_active", "scc_apply_accel", "scc_areq_value", "scc_stop_req",
  # ⑥ 실제 차량
  "v_ego", "a_ego", "gas", "brake", "standstill",
  # ⑦ 속도 제한(km/h): 적용 최고속도(카메라·커브 반영), 설정속도, 제한 원인(cam/bump/section/vturn/route/noo)
  "v_cruise", "set_speed", "apply_source",
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


def _close(out):
  """파일을 닫고 None 을 돌려준다. 닫다가 난 오류(저장 공간 부족 등)는 무시한다."""
  if out is not None:
    try:
      out.close()
    except OSError:
      pass
  return None


def _new_path(directory):
  """YYYYmmdd-HHMMSS.csv. 같은 초에 다시 켜도 앞 파일을 덮어쓰지 않게 뒤에 번호를 붙인다."""
  base = os.path.join(directory, time.strftime("%Y%m%d-%H%M%S"))
  path, n = base + ".csv", 1
  while os.path.exists(path):
    path, n = "%s_%d.csv" % (base, n), n + 1
  return path


_row_error_logged = False


def _safe_row(row_fn, sm):
  """한 줄을 CSV 문자열로. 값을 못 읽는 줄은 건너뛰고(처음 한 번만 출력) 기록기는 계속 돈다."""
  global _row_error_logged
  try:
    return ",".join(row_fn(sm)) + "\n"
  except Exception as e:  # pylint: disable=broad-except
    if not _row_error_logged:
      _row_error_logged = True
      print("trace row skipped:", repr(e))
    return None


def _f(value, digits=3):
  try:
    return f"{float(value):.{digits}f}"
  except (TypeError, ValueError):
    return ""


def _i(value):
  """Bool/숫자/capnp enum 을 정수 문자열로. capnp enum 은 int() 가 안 돼 .raw 를 쓴다."""
  try:
    return str(int(value))
  except (TypeError, ValueError):
    try:
      return str(int(getattr(value, "raw", 0)))
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
    _f(sec_since_boot()),   # logMonoTime 과 같은 시계(time.monotonic 은 절전 시간을 빼서 어긋난다)
    _f(mono['radarState'] * 1e-9), _f(mono['longitudinalPlan'] * 1e-9),
    _f(mono['carControl'] * 1e-9), _f(mono['carState'] * 1e-9),
    _i(lead.status), _i(lead.radar),
    _f(lead.dRel, 2), _f(lead.vRel), _f(lead.vLead), _f(lead.vLeadK), _f(lead.aLeadK), _f(lead.aLeadTau, 2),
    _f(plan.aTarget), _f(plan.vTargetNow), _f(accels[0] if accels else 0.0),
    # T_IDXS 10번째 근처가 약 1초 뒤(계획이 앞으로 무엇을 하려는지)
    _f(accels[10] if len(accels) > 10 else 0.0),
    _i(plan.mpcMode), _i(plan.e2eReason), _i(plan.shouldStop),
    _f(plan.constStopDecel),
    _f(sm['carControl'].actuators.accel), _i(ctl.longControlState), _i(ctl.enabled),
    _i(sm['carControl'].longActive),
    _f(ctl.applyAccel), _f(ctl.aReqValue), _i(ctl.sccStopRequest),
    _f(cs.vEgo), _f(cs.aEgo), _i(cs.gasPressed), _i(cs.brakePressed), _i(cs.standstill),
    _f(ctl.vCruise, 1), _f(ctl.vCruiseCluster, 1),
    str(sm['carControl'].sccSmoother.applySource).replace(",", " ") or "-",
  ]


def main():
  params = Params()
  services = ['radarState', 'longitudinalPlan', 'carControl', 'carState', 'controlsState']
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
        enabled = params.get_bool("LongTraceEnabled")
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
