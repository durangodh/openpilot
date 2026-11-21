#!/usr/bin/env python3
"""운전자 롱 습관 학습(LongLearn) 결과와 추천 설정을 보여 준다. 설정은 바꾸지 않는다.

    cd /data/openpilot && python selfdrive/debug/long_learn.py           # 결과 보기
    cd /data/openpilot && python selfdrive/debug/long_learn.py --reset   # 기록 지우기
"""
import json
import sys

from common.params import Params
from selfdrive.controls.lib.long_learn import MIN_GAP_SAMPLES, MIN_STARTS, MIN_STOPS, LongLearn


def get_int(params, key, default):
  try:
    v = int(params.get(key, encoding="utf8") or 0)
  except (TypeError, ValueError):
    v = 0
  return v if v > 0 else default


def fmt(v, unit="", digits=2):
  return "-" if v is None else f"{v:.{digits}f}{unit}"


def main():
  params = Params()
  if "--reset" in sys.argv:
    params.delete("LongLearnData")
    print("기록을 지웠습니다. (실행 중인 autotuned 는 1분 안에 메모리 값을 다시 쓸 수 있으니, LONG LEARN 을 끈 뒤 지우세요)")
    return
  try:
    learner = LongLearn(json.loads(params.get("LongLearnData", encoding="utf8") or "{}"))
  except (TypeError, ValueError):
    learner = LongLearn()
  try:
    gap_now = int(params.get("PrevCruiseGap", encoding="utf8") or 4)
  except (TypeError, ValueError):
    gap_now = 4
  gap_now = gap_now if 1 <= gap_now <= 4 else 4
  stop_now = get_int(params, "StopDistance", 600) / 100.0
  tf_now = get_int(params, f"TFollowGap{gap_now}", [110, 120, 140, 160][gap_now - 1]) / 100.0
  ratio = get_int(params, "TFollowSpeedRatio", 120) / 100.0
  r = learner.recommend(stop_now, gap_now, tf_now, ratio)
  c = r["counts"]

  print(f"LONG LEARN {'켜짐' if params.get_bool('LongLearn') else '꺼짐'}  (직접 운전할 때만 기록)")
  print(f"기록: 차간 {c['gap']}/{MIN_GAP_SAMPLES}초, 정지 {c['stops']}/{MIN_STOPS}회, 출발 {c['starts']}/{MIN_STARTS}회, "
        f"가속 {c['accel_free'] + c['accel_lead']}회\n")

  print("[차간 시간] (속도 보정 후 TFollowGap 기준)")
  for b in r["gap_bands"]:
    print(f"  {b['kph']:>7} km/h  {b['n']:4d}개  {fmt(b['t_follow'], ' s')}")
  if "t_follow" in r:
    key = f"TFollowGap{gap_now}"
    print(f"  → 현재 GAP {gap_now}: 지금 {tf_now:.2f} s, 내 운전 {r['t_follow']:.2f} s  ⇒ 추천 {key} = {r[key]}")
  else:
    print("  → 기록이 더 필요합니다")

  print("\n[정지]")
  if "StopDistance" in r:
    print(f"  앞차 뒤 정지 거리 {r['stop_distance_m']:.2f} m (지금 {stop_now:.2f} m)  ⇒ 추천 StopDistance = {r['StopDistance']}")
    print(f"  정지 전 최대 감속 {fmt(r['stop_decel_max'], ' m/s²')}, 마지막 1초 평균 감속 {fmt(r['stop_decel_last_1s'], ' m/s²')}")
  else:
    print("  → 기록이 더 필요합니다")

  print("\n[출발] (앞차 출발 뒤)")
  if "start_delay_s" in r:
    print(f"  출발까지 {r['start_delay_s']:.2f} s, 처음 2초 평균 가속 {r['start_accel']:.2f} m/s²")
  else:
    print("  → 기록이 더 필요합니다")

  print("\n[커브] (커브마다 최대 횡가속)")
  if "AutoCurveSpeedFactor" in r:
    print(f"  내 커브 {r['bend_lat_accel']:.2f} m/s² ⇒ 추천 AutoCurveSpeedFactor = {r['AutoCurveSpeedFactor']} "
          f"(지금 {get_int(params, 'AutoCurveSpeedFactor', 120)})")
  else:
    print(f"  → 커브 {c['bends']}/10 개 기록, 더 필요합니다")

  print("\n[앞차 접근 감속] (느린 앞차에 다가가며 최대 감속)")
  if "ComfortBrake" in r:
    print(f"  내 접근 감속 {r['approach_decel']:.2f} m/s² ⇒ 추천 ComfortBrake = {r['ComfortBrake']} "
          f"(지금 {get_int(params, 'ComfortBrake', 250)}, 적용은 지금 값보다 부드러운 쪽만)")
  else:
    print(f"  → 접근 {c['approaches']}/8 회 기록, 더 필요합니다")

  print("\n[가속] (가속 페달, 1초 평균)")
  print(f"  앞차 없음 {fmt(r['accel_free'], ' m/s²')}, 앞차 가까이 {fmt(r['accel_lead'], ' m/s²')}")
  if params.get_bool("LongLearnApply"):
    try:
      applied = json.loads(params.get("LongLearnApplied", encoding="utf8") or "{}")
    except (TypeError, ValueError):
      applied = {}
    print(f"\n[적용 중] LONG LEARN 적용 켜짐: GAP {applied.get('gap', '-')} 차간 {fmt(applied.get('t_follow'), ' s')}, "
          f"정지 거리 {fmt(applied.get('stop_distance'), ' m')} (1분마다 갱신, 하한 1.2 s / 3.5 m)")
  else:
    print("\n추천값만 보여 줍니다. 자동 적용은 설정의 LONG LEARN 적용을 켜세요.")


if __name__ == "__main__":
  main()
