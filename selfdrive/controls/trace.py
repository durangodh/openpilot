#!/usr/bin/env python3
"""가감속·조향 통합 기록기(LongTraceEnabled / LatTraceEnabled).

예전에는 long_trace, lat_trace 두 프로세스가 각각 carState 를 구독했다. carState 구독
자리는 10개뿐이고(cereal/messaging/msgq.h NUM_READERS), 넘으면 모든 구독자를 쫓아내고
다시 붙기를 반복한다("Reader was evicted"). 그래서 한 프로세스, 한 SubMaster 로 합쳤다.

- SubMaster 는 처음 켤 때 한 번만 만들고 다시 만들지 않는다. 닫은 소켓의 자리는 바로
  돌아오지 않아, 껐다 켤 때마다 새로 만들면 자리가 쌓인다.
- 둘 다 꺼져 있으면(켠 적이 없으면) 소켓을 열지 않고 1초마다 설정만 본다.
- 열(CSV 형식)과 파일 위치는 long_trace.py / lat_trace.py 그대로라 7714/trace 와
  분석 스크립트가 그대로 쓴다.
"""
import os
import time

import cereal.messaging as messaging
from common.params import Params
from common.realtime import Ratekeeper
from selfdrive.controls import lat_trace, long_trace
from selfdrive.controls.long_trace import (ERROR_RETRY_S, FLUSH_S, PARAM_POLL_S, RATE_HZ,
                                           _close, _new_path, _rotate, _safe_row)

CLEANUP_S = 3600.0  # 기록을 꺼 둔 날에도 하루 지난 파일을 지우도록 한 시간마다 정리

LONG_SERVICES = ['radarState', 'longitudinalPlan', 'carControl', 'carState', 'controlsState']
LAT_SERVICES = ['lateralPlan', 'controlsState', 'carControl', 'carState', 'liveParameters',
                'liveLocationKalman', 'modelV2']
SERVICES = list(dict.fromkeys(LONG_SERVICES + LAT_SERVICES))


class TraceFile:
  def __init__(self, param, directory, columns, row_fn):
    self.param = param
    self.directory = directory
    self.columns = columns
    self.row_fn = row_fn
    self.enabled = False
    self.out = None
    self.retry_at = 0.0

  def close(self):
    self.out = _close(self.out)

  def write(self, sm, now, flush):
    if now < self.retry_at:
      return
    try:
      if self.out is None:
        os.makedirs(self.directory, exist_ok=True)
        _rotate(self.directory)
        self.out = open(_new_path(self.directory), "w", buffering=1 << 16)
        self.out.write(",".join(self.columns) + "\n")
      row = _safe_row(self.row_fn, sm)
      if row is not None:
        self.out.write(row)
      if flush:
        self.out.flush()
    except OSError:
      # 저장 공간 부족 등: 죽지 않고 파일을 닫은 뒤 잠시 쉬었다 새 파일로 다시 시도한다.
      # 소켓은 그대로 둔다.
      self.close()
      self.retry_at = now + ERROR_RETRY_S


def main():
  params = Params()
  files = [
    TraceFile("LongTraceEnabled", long_trace.TRACE_DIR, long_trace.COLUMNS, long_trace._row),
    TraceFile("LatTraceEnabled", lat_trace.TRACE_DIR, lat_trace.COLUMNS, lat_trace._row),
  ]
  sm = None
  rk = None
  next_param = 0.0
  next_flush = 0.0
  next_cleanup = 0.0
  try:
    while True:
      now = time.monotonic()
      if now >= next_cleanup:
        next_cleanup = now + CLEANUP_S
        for f in files:
          _rotate(f.directory)
      if now >= next_param:
        next_param = now + PARAM_POLL_S
        for f in files:
          f.enabled = params.get_bool(f.param)
          if not f.enabled:
            f.close()

      if not any(f.enabled for f in files):
        rk = None
        time.sleep(PARAM_POLL_S)
        continue

      if sm is None:
        sm = messaging.SubMaster(SERVICES, ignore_avg_freq=SERVICES)
      if rk is None:
        # 꺼져 있던 동안 밀린 주기를 따라잡느라 같은 줄을 몰아 쓰지 않게 새로 시작한다.
        rk = Ratekeeper(RATE_HZ, print_delay_threshold=None)

      sm.update(0)
      if sm.all_alive(['carState']):
        flush = now >= next_flush
        if flush:
          next_flush = now + FLUSH_S
        for f in files:
          if f.enabled:
            f.write(sm, now, flush)
      rk.keep_time()
  finally:
    for f in files:
      f.close()


if __name__ == "__main__":
  main()
