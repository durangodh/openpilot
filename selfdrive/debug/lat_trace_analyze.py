#!/usr/bin/env python3
"""lat_trace CSV 로 조향 반응 지연·추종 오차·직진 흔들림을 본다.

사용법: python3 selfdrive/debug/lat_trace_analyze.py 20261002-221500.csv

분석 구간: 조향 제어 중, 핸들 안 잡음, 차선변경 아님, 18km/h 이상.

[1] 단계별 지연(상호상관, 앞 단계가 바뀐 뒤 다음 단계가 따라오기까지)
    명령      cmd_steer     ← 목표 횡가속(desired curvature × v²)
    핸들 반응 실제 곡률      ← cmd_steer       (EPS·핸들이 명령을 따라가는 시간)
    차체 반응 요레이트 곡률  ← 실제 곡률       (요레이트 신호가 있을 때만)
    전체      실제 곡률      ← 목표 곡률
[2] 추종 오차: 속도·곡선 정도별로 실제/목표 횡가속 비율과 오차
    비율 < 1 이면 덜 돈다(언더), > 1 이면 더 돈다(오버).
[3] 직진 흔들림: 목표가 거의 직진인 구간의 핸들 각도 흔들림 크기와 주기
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from long_trace_analyze import RATE_HZ, best_lag, load  # noqa: E402


def segments(mask):
  """연속으로 True 인 구간 [start, end) 목록."""
  out, start = [], None
  for i, m in enumerate(mask):
    if m and start is None:
      start = i
    elif not m and start is not None:
      out.append((start, i))
      start = None
  if start is not None:
    out.append((start, len(mask)))
  return out


def _corr_at(src, dst, mask, lag_s, dt):
  """lag_s 초 지연에서의 상관계수."""
  k = int(round(lag_s / dt))
  a = src[:len(src) - k] if k else src
  b = dst[k:]
  m = mask[:len(mask) - k] & mask[k:] & np.isfinite(a) & np.isfinite(b)
  if m.sum() < 40:
    return np.nan
  return float(np.corrcoef(a[m], b[m])[0, 1])


def main():
  if len(sys.argv) < 2:
    raise SystemExit(__doc__)
  c = load(sys.argv[1])
  dt = 1.0 / RATE_HZ
  n = len(c["t"])
  print(f"샘플 {n}개, 약 {n * dt / 60:.1f}분")
  for key in ("steer_ratio", "steer_delay"):
    vals = c[key][np.isfinite(c[key])]
    if vals.size:
      print(f"  {key} = {np.median(vals):.3f}")

  v = c["v_ego"]
  mask = ((c["lat_active"] > 0.5) & (c["steer_pressed"] < 0.5) &
          (np.nan_to_num(c["lane_change"]) < 0.5) & (v > 5.0))
  print(f"분석 구간(조향 중·핸들 안 잡음·차선변경 아님·18km/h 이상): {mask.sum() * dt:.0f}초")
  if mask.sum() < 200:
    raise SystemExit("분석할 구간이 너무 짧습니다. 조향을 켜고 더 주행해 주세요.")

  v2 = v * v
  des_lat = c["desired_curv"] * v2
  act_lat = c["curv_actual"] * v2
  with np.errstate(divide="ignore", invalid="ignore"):
    yaw_curv = np.where(v > 1.0, c["yaw_rate"] / v, np.nan)
  yaw_ok = np.nanstd(yaw_curv[mask]) > 1e-5

  print("\n[1] 단계별 지연(상호상관)")
  stages = [
    ("명령 (cmd_steer ← 목표 횡가속)", des_lat, c["cmd_steer"]),
    ("핸들 반응 (실제 곡률 ← cmd_steer)", c["cmd_steer"], c["curv_actual"]),
    ("전체 (실제 곡률 ← 목표 곡률)", c["desired_curv"], c["curv_actual"]),
  ]
  if yaw_ok:
    stages.insert(2, ("차체 반응 (요레이트 곡률 ← 실제 곡률)", c["curv_actual"], yaw_curv))
  for label, src, dst in stages:
    # 차종에 따라 조향 명령·요레이트 부호가 곡률과 반대다. 반대면 뒤집어서 잰다.
    lag, r = best_lag(src, dst, mask, dt)
    lag_n, r_n = best_lag(src, -dst, mask, dt)
    if r_n > r:
      lag, r = lag_n, r_n
      label += " [부호 반대]"
    note = "" if r >= 0.5 else "  (상관 낮음: 참고만)"
    # 지연을 0.5초 줄여도 상관이 거의 같으면 봉우리가 넓어 그 지연값은 믿기 어렵다
    # (예: 토크 명령은 피드포워드가 섞여 실제 곡률과 정확한 시차가 없다).
    if not note and lag >= 0.5:
      sign = -1.0 if "[부호 반대]" in label else 1.0
      r_early = _corr_at(src, sign * dst, mask, lag - 0.5, dt)
      if np.isfinite(r_early) and r - r_early < 0.03:
        note = f"  (봉우리 넓음: {lag - 0.5:.1f}초에서도 r={r_early:.2f}, 지연값은 참고만)"
    print(f"  {label}: {lag * 1000:4.0f} ms, r={r:.2f}{note}")
  if not yaw_ok:
    print("  (요레이트 신호가 없어 차체 반응 단계는 뺐습니다)")

  print("\n[2] 추종 오차 (실제/목표 횡가속 비율, 1.0 이 이상적)")
  print("  속도대        곡선 정도      시간   비율(중앙)  평균오차   RMS오차")
  speed_bins = [(5.0, 50 / 3.6, "18~50km/h"), (50 / 3.6, 80 / 3.6, "50~80km/h"), (80 / 3.6, 60.0, "80km/h+")]
  lat_bins = [(0.5, 1.5, "완만 0.5~1.5"), (1.5, 9.0, "급 1.5 이상")]
  for vlo, vhi, vname in speed_bins:
    for alo, ahi, aname in lat_bins:
      m = mask & (v >= vlo) & (v < vhi) & (np.abs(des_lat) >= alo) & (np.abs(des_lat) < ahi) & \
          np.isfinite(act_lat) & np.isfinite(des_lat)
      if m.sum() < 20:
        continue
      ratio = np.median(act_lat[m] / des_lat[m])
      err = act_lat[m] - des_lat[m]
      signed = np.mean(err * np.sign(des_lat[m]))   # +: 더 돎, -: 덜 돎
      tag = "언더" if ratio < 0.9 else ("오버" if ratio > 1.1 else "양호")
      print(f"  {vname:12s}  {aname:12s} {m.sum() * dt:5.0f}s   {ratio:5.2f} {tag}   "
            f"{signed:+.2f}     {np.sqrt(np.mean(err * err)):.2f}  m/s²")

  print("\n[3] 직진 흔들림 (목표 횡가속 |0.15| m/s² 미만, 3초 이상 이어진 구간)")
  straight = mask & (np.abs(des_lat) < 0.15)
  segs = [(a, b) for a, b in segments(straight) if (b - a) * dt >= 3.0]
  if not segs:
    print("  해당 구간이 없습니다.")
    return
  stds, rates, freqs = [], [], []
  for a, b in segs:
    ang = c["angle_deg"][a:b]
    ang = ang[np.isfinite(ang)]
    if ang.size < 40:
      continue
    ang = ang - np.polyval(np.polyfit(np.arange(ang.size), ang, 1), np.arange(ang.size))
    stds.append(np.std(ang))
    rates.append(np.nanstd(c["rate_deg"][a:b]))
    spec = np.abs(np.fft.rfft(ang * np.hanning(ang.size)))
    f = np.fft.rfftfreq(ang.size, dt)
    band = (f >= 0.2) & (f <= 3.0)
    if band.any():
      freqs.append(f[band][np.argmax(spec[band])])
  total = sum(b - a for a, b in segs) * dt
  print(f"  구간 {len(segs)}개, 합계 {total:.0f}초")
  print(f"  핸들 각도 흔들림(표준편차) 중앙값 {np.median(stds):.2f}°, 각속도 {np.median(rates):.1f}°/s")
  if freqs:
    print(f"  주된 흔들림 주기 약 {1.0 / np.median(freqs):.1f}초 ({np.median(freqs):.2f} Hz)")
  print("  참고: 각도 흔들림이 0.5° 이하면 안정, 1° 이상이면 직진에서 좌우로 꿈틀대는 편입니다.")


if __name__ == "__main__":
  main()
