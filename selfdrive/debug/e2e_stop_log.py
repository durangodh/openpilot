#!/usr/bin/env python3
"""신호 자동 정지 기록(/data/e2e_stop.log)을 주간·야간으로 나눠 보여 준다.

    cd /data/openpilot && python selfdrive/debug/e2e_stop_log.py          # 요약 + 최근 20줄
    cd /data/openpilot && python selfdrive/debug/e2e_stop_log.py --all    # 전부

AUTO   자동 정지가 걸린 순간의 모델 값
MISSED 운전자가 브레이크로 멈춘 정지(앞차 없음) = 자동 정지가 놓친 곳
mx 모델 경로 끝 거리(m), mv 모델 끝 속도(m/s), mv0 모델 처음 속도, *_min3s 브레이크 전 3초 최소값
"""
import json
import sys
from statistics import median

from selfdrive.controls.lib.e2e_stop_log import LOG_PATH


def is_day(hour):
  return 7 <= hour < 18


def main():
  rows = []
  for path in (LOG_PATH + ".1", LOG_PATH):
    try:
      with open(path) as f:
        rows += [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
      pass
  if not rows:
    print("기록이 없습니다. 주행 후 다시 보세요.")
    return
  for label, day in (("주간 07~18시", True), ("야간", False)):
    part = [r for r in rows if is_day(r.get("hour", 12)) == day]
    auto = [r for r in part if r["kind"] == "AUTO"]
    missed = [r for r in part if r["kind"] == "MISSED"]
    total = len(auto) + len(missed)
    rate = f"{100 * len(auto) / total:.0f}%" if total else "-"
    print(f"[{label}] 자동 {len(auto)}회, 놓침 {len(missed)}회, 자동 비율 {rate}")
    for name, group in (("자동", auto), ("놓침", missed)):
      if group:
        print(f"  {name}: mv 중앙 {median(r['mv'] for r in group):.1f}, mv_min3s 중앙 {median(r['mv_min3s'] for r in group):.1f}, "
              f"mx_min3s 중앙 {median(r['mx_min3s'] for r in group):.0f} m, 속도 중앙 {median(r['v'] for r in group) * 3.6:.0f} km/h")
  print()
  for r in rows if "--all" in sys.argv else rows[-20:]:
    print(f"{r['kind']:6} {r['hour']:02d}시 {r['v'] * 3.6:5.1f}km/h mx {r['mx']:6.1f} mv {r['mv']:5.2f} mv0 {r['mv0']:5.2f} "
          f"min3s mx {r['mx_min3s']:6.1f} mv {r['mv_min3s']:5.2f} lead {r['lead']:6.1f} {r['signal']}")


if __name__ == "__main__":
  main()
