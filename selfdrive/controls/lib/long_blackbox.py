"""종제어 블랙박스.

주행 중 최근 20초를 메모리에만 20Hz 로 들고 있다가, 아래 사건이 생기면
사건 전 20초 + 후 2~3초를 CSV 로 남긴다. 평소엔 파일을 쓰지 않는다.
  - stop : 20km/h 이상에서 롱컨으로 감속해 완전 정지
  - hard : 실제 감속 또는 명령 감속이 -2.5m/s² 이하 (급제동)
  - start: 1초 이상 정차한 뒤 롱컨으로 출발 (출발 후 8초까지 기록 → 출발 가속 확인)
저장 위치: /data/long_logs/  (최근 20개만 유지)

전체 주행 기록(drive_*.csv)도 함께 남긴다.
  - 시동(controlsd 시작)마다 파일 1개, 10Hz, 30초마다 모아서 이어쓰기
  - 정차 중(롱컨 꺼짐·속도 0)은 기록 안 함
  - drive_* 전체가 200MB 를 넘으면 오래된 파일부터 삭제 (시간당 약 9MB, 약 20시간 분량)
"""
import os
import threading
import time
from collections import deque

from common.numpy_fast import interp
from selfdrive.controls.lib.drive_helpers import CONTROL_N
from selfdrive.modeld.constants import T_IDXS

LOG_DIR = "/data/long_logs"
LOG_KEEP = 20
SAMPLE_DIV = 5
PRE_SAMPLES = 400
POST_STOP_SAMPLES = 40
POST_HARD_SAMPLES = 60
POST_START_SAMPLES = 160
START_STANDSTILL_S = 1.0
START_SPEED = 0.5
STOP_ARM_SPEED = 20.0 / 3.6
STOP_SPEED = 0.3
HARD_DECEL = -2.5
HARD_REARM_S = 20.0

DRIVE_DIV = 2
DRIVE_FLUSH_S = 30.0
DRIVE_MAX_BYTES = 200 * 1024 * 1024

HEADER = ("t,vEgo_kph,aEgo,longActive,longState,brake,gas,"
          "plan_v,plan_a,a_lower,a_upper,a_cmd,out_accel,pid_p,pid_i,pid_f,"
          "lead,dRel,vLead_kph,aLeadK,prob,radar,xState,trafficState,"
          "src,tFollow,desiredDist,mpcMode,"
          "vis_prob,vis_d,vis_v_kph,"
          "ssLatched,leadRelSamples,leadMissFrames,startReqFrames,departAssist,departRelease,"
          "vCruise,applyMax,targetSpeed,curveSpeed,camLimit,sectionLimit,"
          "e2eReason,modelEndX,modelEndV_kph")


def _write(rows, name):
  try:
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, name), "w") as f:
      f.write(HEADER + "\n")
      f.write("\n".join(rows) + "\n")
    files = sorted(n for n in os.listdir(LOG_DIR) if n.startswith("long_"))
    for n in files[:-LOG_KEEP]:
      os.remove(os.path.join(LOG_DIR, n))
  except Exception:
    pass


def _fmt(r):
  return ",".join(f"{v:.3f}" if isinstance(v, float) else str(v) for v in r)


def _append_drive(rows, name, write_header):
  try:
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, name), "a") as f:
      if write_header:
        f.write(HEADER + "\n")
      f.write("\n".join(rows) + "\n")
    files = sorted(n for n in os.listdir(LOG_DIR) if n.startswith("drive_"))
    sizes = {n: os.path.getsize(os.path.join(LOG_DIR, n)) for n in files}
    total = sum(sizes.values())
    for n in files:
      if total <= DRIVE_MAX_BYTES or n == name:
        break
      os.remove(os.path.join(LOG_DIR, n))
      total -= sizes[n]
  except Exception:
    pass


class LongBlackbox:
  def __init__(self):
    self.buf = deque(maxlen=PRE_SAMPLES + POST_START_SAMPLES)
    self.count = 0
    self.t0 = time.monotonic()
    self.stop_armed = False
    self.hard_ready_at = 0.0
    self.post_left = -1
    self.pending_name = ""
    self.standstill_since = None

    self.drive_name = "drive_" + time.strftime("%Y%m%d_%H%M%S") + ".csv"
    self.drive_rows = []
    self.drive_header_done = False
    self.drive_last_flush = self.t0
    self.drive_div = 0

  def update(self, CS, long_plan, lead, radar_valid, actuators, LoC, long_active, t_since_plan,
             vision_lead=None, v_cruise_kph=0.0, apply_max_speed=0.0, cruise_helper=None, model=None):
    self.count += 1
    if self.count % SAMPLE_DIV:
      return
    try:
      now = time.monotonic()
      v_now = a_now = a_lower = a_upper = a_cmd = 0.0
      speeds = long_plan.speeds
      accels = long_plan.accels
      if len(speeds) == CONTROL_N and len(accels) == CONTROL_N:
        t = T_IDXS[:CONTROL_N]
        v_now = interp(t_since_plan, t, speeds)
        a_now = interp(t_since_plan, t, accels)
        dl = LoC.actuator_delay_lower
        du = LoC.actuator_delay_upper
        a_lower = 2 * (interp(dl + t_since_plan, t, speeds) - v_now) / dl - a_now
        a_upper = 2 * (interp(du + t_since_plan, t, speeds) - v_now) / du - a_now
        a_cmd = min(a_lower, a_upper)
      pid = LoC.pid
      has_lead = bool(radar_valid and lead.status)
      vis_prob = vis_d = vis_v = 0.0
      if vision_lead is not None:
        try:
          vis_prob = float(vision_lead.prob)
          if len(vision_lead.x):
            vis_d = float(vision_lead.x[0])
            vis_v = float(vision_lead.v[0]) * 3.6
        except Exception:
          pass
      dep = getattr(LoC, "departure_assist", None)
      ch = cruise_helper
      model_x = model_v = 0.0
      if model is not None:
        try:
          if len(model.position.x):
            model_x = float(model.position.x[-1])
          if len(model.velocity.x):
            model_v = float(model.velocity.x[-1]) * 3.6
        except Exception:
          pass
      self.buf.append(_fmt((
        round(now - self.t0, 2), float(CS.vEgo * 3.6), float(CS.aEgo), int(bool(long_active)),
        int(LoC.long_control_state), int(CS.brakePressed), int(CS.gasPressed),
        float(v_now * 3.6), float(a_now), float(a_lower), float(a_upper), float(a_cmd),
        float(actuators.accel), float(getattr(pid, "p", 0.0)), float(getattr(pid, "i", 0.0)),
        float(getattr(pid, "f", 0.0)),
        int(has_lead), float(lead.dRel if has_lead else 0.0), float(lead.vLead * 3.6 if has_lead else 0.0),
        float(lead.aLeadK if has_lead else 0.0), float(lead.modelProb if has_lead else 0.0),
        int(bool(lead.radar) if has_lead else 0),
        int(getattr(long_plan, "xState", 0)), int(getattr(long_plan, "trafficState", 0)),
        str(getattr(long_plan, "longitudinalPlanSource", "")), float(getattr(long_plan, "tFollow", 0.0)),
        float(getattr(long_plan, "desiredDistance", 0.0)), int(getattr(long_plan, "mpcMode", 0)),
        float(vis_prob), float(vis_d), float(vis_v),
        int(bool(getattr(LoC, "standstill_lead_latched", False))), int(getattr(LoC, "lead_release_samples", 0)),
        int(getattr(LoC, "lead_missing_frames", 0)), int(getattr(LoC, "start_request_frames", 0)),
        int(bool(getattr(dep, "active", False))), int(bool(getattr(LoC, "departure_release_active", False))),
        float(v_cruise_kph), float(apply_max_speed), float(getattr(ch, "target_speed", 0.0) or 0.0),
        float(min(getattr(ch, "curve_speed_ms", 0.0) or 0.0, 100.0) * 3.6),
        float(getattr(ch, "cam_limit_est", 0.0) or 0.0), float(getattr(ch, "section_limit_est", 0.0) or 0.0),
        int(getattr(long_plan, "e2eReason", 0)), float(model_x), float(model_v),
      )))

      self.drive_div += 1
      if self.drive_div % DRIVE_DIV == 0 and (long_active or CS.vEgo > STOP_SPEED):
        self.drive_rows.append(self.buf[-1])
      if now - self.drive_last_flush >= DRIVE_FLUSH_S:
        self.drive_last_flush = now
        if self.drive_rows:
          rows, self.drive_rows = self.drive_rows, []
          threading.Thread(target=_append_drive,
                           args=(rows, self.drive_name, not self.drive_header_done), daemon=True).start()
          self.drive_header_done = True

      if CS.vEgo < STOP_SPEED:
        if self.standstill_since is None:
          self.standstill_since = now
      launch = (self.standstill_since is not None and CS.vEgo > START_SPEED and
                now - self.standstill_since >= START_STANDSTILL_S)
      if CS.vEgo >= STOP_SPEED and (CS.vEgo > START_SPEED or not long_active):
        if not launch:
          self.standstill_since = None

      if self.post_left < 0 and long_active:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        if CS.vEgo > STOP_ARM_SPEED:
          self.stop_armed = True
        if launch and not CS.gasPressed:
          self.standstill_since = None
          self.post_left = POST_START_SAMPLES
          self.pending_name = f"long_{stamp}_start.csv"
        elif self.stop_armed and CS.vEgo < STOP_SPEED:
          self.stop_armed = False
          self.post_left = POST_STOP_SAMPLES
          self.pending_name = f"long_{stamp}_stop.csv"
        elif now >= self.hard_ready_at and min(CS.aEgo, actuators.accel) <= HARD_DECEL and not CS.brakePressed:
          self.hard_ready_at = now + HARD_REARM_S
          self.post_left = POST_HARD_SAMPLES
          self.pending_name = f"long_{stamp}_hard.csv"
      elif not long_active:
        self.stop_armed = False

      if self.post_left >= 0:
        if self.post_left == 0:
          threading.Thread(target=_write, args=(list(self.buf), self.pending_name), daemon=True).start()
        self.post_left -= 1
    except Exception:
      pass
