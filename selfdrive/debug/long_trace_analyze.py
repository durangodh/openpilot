#!/usr/bin/env python3
"""long_trace CSV 로 앞차 반응이 어느 단계에서 늦는지 잰다.

사용법: python3 selfdrive/debug/long_trace_analyze.py 20261002-221500.csv

단계(앞 단계 신호가 바뀐 뒤 다음 단계가 따라 바뀌기까지 걸린 시간):
  ② 앞차 가속 추정   a_lead_k        ← 레이더 앞차 속도 v_lead 의 실제 변화
  ④ 계획             plan_a_target   ← a_lead_k
  ⑤ 명령             cmd_accel       ← plan_a_target
  ⑥ 차량             a_ego           ← cmd_accel
  전체               a_ego           ← 레이더 앞차 속도 변화

두 가지로 잰다.
  1) 상호상관: 앞차가 있고 크루즈가 켜진 구간 전체에서 두 신호가 가장 잘 겹치는 지연.
  2) 앞차 감속 시작 사건: 앞차가 감속하기 시작한 순간부터 각 단계가 반응하기까지.
"""
import csv
import sys

import numpy as np

RATE_HZ = 20.0
MAX_LAG_S = 2.0


def load(path):
  with open(path, newline="") as f:
    rows = list(csv.DictReader(f))
  if not rows:
    raise SystemExit("빈 파일입니다.")
  cols = {}
  for key in rows[0].keys():
    vals = []
    for r in rows:
      try:
        vals.append(float(r[key]))
      except (TypeError, ValueError):
        vals.append(np.nan)
    cols[key] = np.array(vals)
  return cols


def centered_derivative(x, dt, half_window=2):
  """가운데 차분(지연 없음). half_window 샘플 앞뒤 차이."""
  d = np.full_like(x, np.nan)
  k = half_window
  d[k:-k] = (x[2 * k:] - x[:-2 * k]) / (2 * k * dt)
  return d


def best_lag(src, dst, mask, dt):
  """dst 가 src 를 따라가는 지연(초)과 상관계수. 양수면 dst 가 늦다."""
  best = (np.nan, -1.0)
  for lag in range(0, int(MAX_LAG_S / dt) + 1):
    a = src[:len(src) - lag] if lag else src
    b = dst[lag:]
    m = mask[:len(mask) - lag] & mask[lag:] & np.isfinite(a) & np.isfinite(b)
    if m.sum() < 40:
      continue
    aa, bb = a[m] - a[m].mean(), b[m] - b[m].mean()
    denom = np.sqrt((aa * aa).sum() * (bb * bb).sum())
    if denom <= 1e-9:
      continue
    r = float((aa * bb).sum() / denom)
    if r > best[1]:
      best = (lag * dt, r)
  return best


def first_cross(sig, start, level, below, limit):
  end = min(len(sig), start + limit)
  for i in range(start, end):
    v = sig[i]
    if np.isfinite(v) and ((v <= level) if below else (v >= level)):
      return i
  return None


def main():
  if len(sys.argv) < 2:
    raise SystemExit(__doc__)
  c = load(sys.argv[1])
  dt = 1.0 / RATE_HZ
  n = len(c["t"])
  print(f"샘플 {n}개, 약 {n * dt / 60:.1f}분")

  # 메시지 자체 지연(기록 시각 - 메시지 생성 시각): 기록기 쪽에서 본 신선도.
  for name, key in (("레이더", "radar_t"), ("계획", "plan_t"), ("명령", "cc_t"), ("차량", "cs_t")):
    age = c["t"] - c[key]
    age = age[np.isfinite(age) & (c[key] > 0)]
    if age.size:
      print(f"  {name} 메시지 나이 중앙값 {np.median(age) * 1000:.0f} ms")

  lead = (c["lead"] > 0.5) & (c["enabled"] > 0.5) & (c["gas"] < 0.5) & (c["brake"] < 0.5)
  print(f"분석 구간(앞차 있음·크루즈 켜짐·페달 안 밟음): {lead.sum() * dt:.0f}초")
  if lead.sum() < 100:
    raise SystemExit("분석할 구간이 너무 짧습니다. 정체 구간을 더 주행해 주세요.")

  a_lead_true = centered_derivative(c["v_lead"], dt)
  stages = [
    ("② 앞차 가속 추정 (a_lead_k ← 레이더 속도 변화)", a_lead_true, c["a_lead_k"]),
    ("④ 계획 (plan_a_target ← a_lead_k)", c["a_lead_k"], c["plan_a_target"]),
    ("⑤ 명령 (cmd_accel ← plan_a_target)", c["plan_a_target"], c["cmd_accel"]),
    ("⑥ 차량 (a_ego ← cmd_accel)", c["cmd_accel"], c["a_ego"]),
    ("전체 (a_ego ← 레이더 속도 변화)", a_lead_true, c["a_ego"]),
  ]
  print("\n[1] 상호상관 지연")
  for label, src, dst in stages:
    lag, r = best_lag(src, dst, lead, dt)
    note = "" if r >= 0.5 else "  (상관 낮음: 참고만)"
    print(f"  {label}: {lag * 1000:4.0f} ms, r={r:.2f}{note}")

  print("\n[2] 앞차 감속 시작 사건 (레이더 속도 기준 -0.8 m/s² 이하로 떨어진 순간)")
  limit = int(4.0 / dt)
  events = []
  i = 1
  while i < n:
    # 처음 -0.8 아래로 내려간 순간이고, 그 직전 0.25~1초 동안은 감속이 없던 경우만.
    if lead[i] and np.isfinite(a_lead_true[i]) and a_lead_true[i] <= -0.8 and \
       np.isfinite(a_lead_true[i - 1]) and a_lead_true[i - 1] > -0.8 and i >= 20 and \
       np.all(np.nan_to_num(a_lead_true[i - 20:i - 5], nan=0.0) > -0.3):
      base_plan = c["plan_a_target"][i]
      base_cmd = c["cmd_accel"][i]
      base_ego = c["a_ego"][i]
      ev = {
        "t": i * dt,
        "a_lead_k": first_cross(c["a_lead_k"], i, -0.5, True, limit),
        "plan": first_cross(c["plan_a_target"], i, base_plan - 0.3, True, limit),
        "cmd": first_cross(c["cmd_accel"], i, base_cmd - 0.3, True, limit),
        "ego": first_cross(c["a_ego"], i, base_ego - 0.3, True, limit),
      }
      events.append((i, ev))
      i += limit
    else:
      i += 1
  if not events:
    print("  해당 사건이 없습니다.")
  for i, ev in events:
    def ms(j):
      return "  -  " if j is None else f"{(j - i) * dt * 1000:4.0f}"
    print(f"  {ev['t']:7.1f}s  추정 {ms(ev['a_lead_k'])}  계획 {ms(ev['plan'])}  "
          f"명령 {ms(ev['cmd'])}  차량 {ms(ev['ego'])}  (ms, 앞차 감속 시작부터)")
  if events:
    def med(key):
      vals = [(ev[key] - i) * dt * 1000 for i, ev in events if ev[key] is not None]
      return f"{np.median(vals):.0f} ms ({len(vals)}건)" if vals else "-"
    print(f"  중앙값: 추정 {med('a_lead_k')}, 계획 {med('plan')}, 명령 {med('cmd')}, 차량 {med('ego')}")


if __name__ == "__main__":
  main()
