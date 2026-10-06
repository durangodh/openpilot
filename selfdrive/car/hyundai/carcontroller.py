import os
import threading
import time
from collections import deque
from random import randint

from cereal import car
from common.realtime import DT_CTRL
from common.numpy_fast import clip
from selfdrive.car import apply_std_steer_torque_limits
from selfdrive.car.hyundai.hyundaican import create_lkas11, create_clu11, \
  create_scc11, create_scc12, create_scc13, create_scc14, \
  create_mdps12, create_lfahda_mfc, create_hda_mfc
from selfdrive.car.hyundai.scc_smoother import SccSmoother
from selfdrive.car.hyundai.values import Buttons, CAR, FEATURES, CarControllerParams
from selfdrive.car.hyundai.cruise_buttons import button_pressed_in_samples
from opendbc.can.packer import CANPacker
from common.conversions import Conversions as CV
from common.params import Params
from selfdrive.controls.lib.longcontrol import LongCtrlState
from selfdrive.road_speed_limiter import road_speed_limiter_get_active

VisualAlert = car.CarControl.HUDControl.VisualAlert


def should_request_scc_standstill(stopping, soft_hold_scc, car_standstill, v_ego, pre_release=False):
  """Assert Hyundai StopReq only after the vehicle has actually stopped.

  pre_release (EarlyStopReqRelease): LongControl saw the stopped lead start to
  move, so StopReq is dropped early while the negative brake request stays, to
  let the SCC begin leaving its standstill hold before the launch command.
  Soft hold (driver brake) always keeps StopReq.
  """
  actual_standstill = car_standstill or v_ego < 0.1
  if soft_hold_scc:
    return bool(actual_standstill)
  return bool(stopping and actual_standstill and not pre_release)


def process_hud_alert(enabled, fingerprint, hud_control):

  sys_warning = (hud_control.visualAlert in (VisualAlert.steerRequired, VisualAlert.ldw))

  # initialize to no line visible
  sys_state = 1
  if hud_control.leftLaneVisible and hud_control.rightLaneVisible or sys_warning:  # HUD alert only display when LKAS status is active
    sys_state = 3 if enabled or sys_warning else 4
  elif hud_control.leftLaneVisible:
    sys_state = 5
  elif hud_control.rightLaneVisible:
    sys_state = 6

  # initialize to no warnings
  left_lane_warning = 0
  right_lane_warning = 0
  if hud_control.leftLaneDepart:
    left_lane_warning = 1
  if hud_control.rightLaneDepart:
    right_lane_warning = 1

  return sys_warning, sys_state, left_lane_warning, right_lane_warning


# ── MDPS 폴트 블랙박스 ──────────────────────────────────────────────────────
# loggerd 없이도 폴트 원인을 볼 수 있게, 최근 6초를 메모리에만 들고 있다가
# steerFaultTemporary 가 켜지는 순간 전후(6초 전 ~ 1초 후)를 CSV 로 남긴다.
# 평소엔 파일을 쓰지 않는다. 저장 위치: /data/steer_fault_logs/
FAULT_LOG_DIR = "/data/steer_fault_logs"
FAULT_PRE_FRAMES = 600    # 100Hz × 6s
FAULT_POST_FRAMES = 100   # 100Hz × 1s
FAULT_LOG_KEEP = 10
FAULT_LOG_HEADER = ("frame,vEgo,angle,rate,drvTq,drvPressed,enabled,latActive,"
                    "reqSteer,applySteer,applyLast,cutSteer,angleCnt,"
                    "toiUnavail,toiFlt,failStat,mdpsOutTq,mdpsColTq,pid_p,pid_i,pid_f")


def _write_fault_log(rows, stamp):
  try:
    os.makedirs(FAULT_LOG_DIR, exist_ok=True)
    path = os.path.join(FAULT_LOG_DIR, f"fault_{stamp}.csv")
    with open(path, "w") as f:
      f.write(FAULT_LOG_HEADER + "\n")
      for r in rows:
        f.write(",".join(f"{v:.4f}" if isinstance(v, float) else str(v) for v in r) + "\n")
    files = sorted(n for n in os.listdir(FAULT_LOG_DIR) if n.startswith("fault_"))
    for n in files[:-FAULT_LOG_KEEP]:
      os.remove(os.path.join(FAULT_LOG_DIR, n))
  except Exception:
    pass


class CarController:
  def __init__(self, dbc_name, CP, VM):
    self.car_fingerprint = CP.carFingerprint
    self.params = CarControllerParams(CP)
    self.packer = CANPacker(dbc_name)
    self.frame = 0

    self.apply_steer_last = 0
    self.accel = 0
    self.lkas11_cnt = 0
    self.scc12_cnt = -1

    self.resume_cnt = 0
    self.resume_wait_timer = 0

    self.turning_signal_timer = 0
    self.longcontrol = CP.openpilotLongitudinalControl
    self.scc_live = not CP.radarOffCan

    self.turning_indicator_alert = False
    # NOO is steering a turn in the same direction as the driver's blinker.
    # Read by the interface (one frame later) to skip the low-speed blinker cut.
    self.noo_turn_keep = False

    param = Params()

    self.mad_mode_enabled = param.get_bool('MadModeEnabled')
    self.keep_steering_turn_signals = param.get_bool('KeepSteeringTurnSignals')
    self.haptic_feedback_speed_camera = param.get_bool('HapticFeedbackWhenSpeedCamera')
    self.op_params = param

    self.scc_smoother = SccSmoother()
    self.soft_hold_mode = int(clip(param.get_int("SoftHoldMode"), 0, 2))
    self.last_blinker_frame = 0
    self.prev_active_cam = False
    self.active_cam_timer = 0
    self.last_active_cam_frame = 0

    self.angle_limit_counter = 0
    self.cut_steer_frames = 0
    self.cut_steer = False

    self.steer_fault_max_angle = CP.steerFaultMaxAngle
    self.steer_fault_max_frames = CP.steerFaultMaxFrames

    self.fault_buf = deque(maxlen=FAULT_PRE_FRAMES + FAULT_POST_FRAMES)
    self.fault_prev = False
    self.fault_post_left = -1
    self.fault_stamp = ""

  def _record_fault(self, CC, CS, controls, apply_steer, lkas_active, cut_steer_temp):
    try:
      m = CS.mdps12
      pid = getattr(getattr(controls, "LaC", None), "pid", None)
      self.fault_buf.append((
        self.frame, float(CS.out.vEgo), float(CS.out.steeringAngleDeg), float(CS.out.steeringRateDeg),
        float(CS.out.steeringTorque), int(CS.out.steeringPressed), int(CC.enabled), int(bool(lkas_active)),
        float(CC.actuators.steer), int(apply_steer), int(self.apply_steer_last), int(cut_steer_temp),
        int(self.angle_limit_counter),
        int(m.get("CF_Mdps_ToiUnavail", -1)), int(m.get("CF_Mdps_ToiFlt", -1)), int(m.get("CF_Mdps_FailStat", -1)),
        float(m.get("CR_Mdps_OutTq", 0.0)), float(m.get("CR_Mdps_StrColTq", 0.0)),
        float(getattr(pid, "p", 0.0)), float(getattr(pid, "i", 0.0)), float(getattr(pid, "f", 0.0)),
      ))

      fault = bool(CS.out.steerFaultTemporary or CS.out.steerFaultPermanent)
      if fault and not self.fault_prev and self.fault_post_left < 0:
        self.fault_post_left = FAULT_POST_FRAMES
        self.fault_stamp = time.strftime("%Y%m%d_%H%M%S")
      self.fault_prev = fault

      if self.fault_post_left >= 0:
        if self.fault_post_left == 0:
          threading.Thread(target=_write_fault_log,
                           args=(list(self.fault_buf), self.fault_stamp), daemon=True).start()
        self.fault_post_left -= 1
    except Exception:
      pass

  @staticmethod
  def _noo_turn_matches_blinker(CS, controls):
    try:
      noo_turn = int(controls.sm['lateralPlan'].nooTurnDirection)
    except (AttributeError, KeyError, TypeError, ValueError):
      return False
    # Also covers the 0.5 s hold after the blinker goes off; only an opposite
    # blinker keeps the cut.
    left, right = CS.out.leftBlinker, CS.out.rightBlinker
    return (noo_turn < 0 and not right) or (noo_turn > 0 and not left)

  def update(self, CC, CS, controls):
    actuators = CC.actuators
    hud_control = CC.hudControl
    pcm_cancel_cmd = CC.cruiseControl.cancel

    # Steering Torque
    new_steer = int(round(actuators.steer * self.params.STEER_MAX))
    apply_steer = apply_std_steer_torque_limits(new_steer, self.apply_steer_last, CS.out.steeringTorque, self.params)

    # disable when temp fault is active, or below LKA minimum speed
    lkas_active = CC.latActive

    # Disable steering while turning blinker on and speed below 60 kph
    if CS.out.leftBlinker or CS.out.rightBlinker:
      self.turning_signal_timer = 0.5 / DT_CTRL  # Disable for 0.5 Seconds after blinker turned off
    # A driver blinker that matches an active NOO turn is the expected turn
    # signal, not a request to hand the wheel back.
    self.noo_turn_keep = self._noo_turn_matches_blinker(CS, controls)
    if self.turning_indicator_alert and not self.noo_turn_keep:  # set and clear by interface
      lkas_active = 0
    if self.turning_signal_timer > 0:
      self.turning_signal_timer -= 1

    if not lkas_active:
      apply_steer = 0

    self.apply_steer_last = apply_steer

    sys_warning, sys_state, left_lane_warning, right_lane_warning = process_hud_alert(CC.enabled, self.car_fingerprint, hud_control)

    if self.haptic_feedback_speed_camera:
      if self.prev_active_cam != controls.cruise_helper.active_cam:
        self.prev_active_cam = controls.cruise_helper.active_cam
        if controls.cruise_helper.active_cam:
          if (self.frame - self.last_active_cam_frame) * DT_CTRL > 10.0:
            self.active_cam_timer = int(1.5 / DT_CTRL)
            self.last_active_cam_frame = self.frame

      if self.active_cam_timer > 0:
        self.active_cam_timer -= 1
        left_lane_warning = right_lane_warning = 1

    clu11_speed = CS.clu11["CF_Clu_Vanz"]
    enabled_speed = 38 if CS.is_set_speed_in_mph else 60
    if clu11_speed > enabled_speed or not lkas_active:
      enabled_speed = clu11_speed

    if self.frame == 0:  # initialize counts from last received count signals
      self.lkas11_cnt = CS.lkas11["CF_Lkas_MsgCount"]

    self.lkas11_cnt = (self.lkas11_cnt + 1) % 0x10

    cut_steer_temp = False

    if self.steer_fault_max_angle > 0:
      if lkas_active and abs(CS.out.steeringAngleDeg) >= self.steer_fault_max_angle:
        self.angle_limit_counter += 1
      else:
        self.angle_limit_counter = 0

      # stop requesting torque to avoid 90 degree fault and hold torque with induced temporary fault
      # two cycles avoids race conditions every few minutes
      if self.angle_limit_counter > self.steer_fault_max_frames:
        self.cut_steer = True
      elif self.cut_steer_frames > 1:
        self.cut_steer_frames = 0
        self.cut_steer = False

      if self.cut_steer:
        cut_steer_temp = True
        self.angle_limit_counter = 0
        self.cut_steer_frames += 1

    self._record_fault(CC, CS, controls, apply_steer, lkas_active, cut_steer_temp)

    can_sends = []
    can_sends.append(create_lkas11(self.packer, self.frame, self.car_fingerprint, apply_steer, lkas_active,
                                   CS.lkas11, sys_warning, sys_state, CC.enabled, hud_control.leftLaneVisible, hud_control.rightLaneVisible,
                                   left_lane_warning, right_lane_warning, 0, False, cut_steer_temp))

    if CS.mdps_bus or CS.scc_bus == 1:  # send lkas11 bus 1 if mdps or scc is on bus 1
      can_sends.append(create_lkas11(self.packer, self.frame, self.car_fingerprint, apply_steer, lkas_active,
                                     CS.lkas11, sys_warning, sys_state, CC.enabled, hud_control.leftLaneVisible, hud_control.rightLaneVisible,
                                     left_lane_warning, right_lane_warning, 1, False, cut_steer_temp))

    if self.frame % 2 and CS.mdps_bus:  # send clu11 to mdps if it is not on bus 0
      can_sends.append(create_clu11(self.packer, CS.mdps_bus, CS.clu11, Buttons.NONE, enabled_speed))

    if pcm_cancel_cmd and (self.longcontrol and not self.mad_mode_enabled):
      can_sends.append(create_clu11(self.packer, CS.scc_bus, CS.clu11, Buttons.CANCEL, clu11_speed))

    if CS.mdps_bus or self.car_fingerprint in FEATURES["send_mdps12"]:  # send mdps12 to LKAS to prevent LKAS error
      can_sends.append(create_mdps12(self.packer, self.frame, CS.mdps12))

    # Match aPilot C2: legacy standstill RES pulses are only for stock ACC.
    # Openpilot longitudinal control already commands the SCC messages needed
    # to launch, and an extra RES pulse makes the cluster report an invalid
    # cruise-setting condition during an otherwise normal departure.
    if not self.longcontrol:
      self.update_auto_resume(CC, CS, clu11_speed, can_sends)
    self.update_scc(CC, CS, actuators, controls, hud_control, can_sends)

    # 20 Hz LFA MFA message
    if self.frame % 5 == 0:
      activated_hda = road_speed_limiter_get_active()
      # activated_hda: 0 - off, 1 - main road, 2 - highway
      if self.car_fingerprint in FEATURES["send_lfa_mfa"]:
        can_sends.append(create_lfahda_mfc(self.packer, CC.enabled, activated_hda))
      elif CS.has_lfa_hda:
        can_sends.append(create_hda_mfc(self.packer, activated_hda, CS, hud_control.leftLaneVisible, hud_control.rightLaneVisible))

    new_actuators = actuators.copy()
    new_actuators.steer = apply_steer / self.params.STEER_MAX
    new_actuators.accel = self.accel

    self.frame += 1
    return new_actuators, can_sends

  def update_auto_resume(self, CC, CS, clu11_speed, can_sends):
    # CC.cruiseControl.resume is already gated by enabled, standstill and
    # the planner's departure trajectory. Do not gate it again
    # on SCC11 ACC_ObjDist: some SCC firmwares update that value late (or not
    # at all while latched), which prevents the RES command from ever firing.
    physical_button_pressed = button_pressed_in_samples(
      CS.cruise_buttons, getattr(CS, 'cruise_button_samples', ()))
    if CC.cruiseControl.resume and not CS.out.gasPressed and not physical_button_pressed:
      if self.scc_smoother.is_active(self.frame):
        pass

      elif self.resume_wait_timer > 0:
        self.resume_wait_timer -= 1

      else:
        can_sends.append(create_clu11(self.packer, CS.scc_bus, CS.clu11, Buttons.RES_ACCEL, clu11_speed))
        self.resume_cnt += 1

        if self.resume_cnt >= int(randint(4, 5) * 2):
          self.resume_cnt = 0
          self.resume_wait_timer = int(randint(20, 25) * 2)

    else:
      self.resume_cnt = 0
      self.resume_wait_timer = 0

  def update_scc(self, CC, CS, actuators, controls, hud_control, can_sends):

    # scc smoother
    self.scc_smoother.update(CC.enabled, can_sends, self.packer, CC, CS, self.frame, controls)

    if self.frame % 100 == 0:
      self.soft_hold_mode = int(clip(self.op_params.get_int("SoftHoldMode"), 0, 2))
    soft_hold = bool(hud_control.softHold)
    soft_hold_scc = soft_hold and self.soft_hold_mode == 2 and CS.out.brakePressed
    stopping = controls.LoC.long_control_state == LongCtrlState.stopping
    jerk_stopping = stopping or soft_hold
    pre_release = stopping and bool(getattr(controls.LoC, 'stopreq_release_active', False))
    scc_stop_request = should_request_scc_standstill(
      stopping, soft_hold_scc, CS.out.standstill, CS.out.vEgo, pre_release)

    # Smoothing is LongControl's, except the launch: since 2026-10-06 the
    # starting request steps to startAccel and SCC14 carries the launch jerk
    # (START JERK LIMIT, lead launch >= 2.5, 5.0 after 2.5 s) like apilot-c2.
    # Otherwise SCC14 gets generous limits; stopping keeps the hold limits.
    jerk_limit = 5.0
    if jerk_stopping:
      jerk_upper = 0.5
      jerk_lower = jerk_limit
    else:
      jerk_upper = jerk_lower = jerk_limit
      # apilot-c2 방식: 출발 순간 명령은 한 번에 올리고(LongControl starting), 실제
      # 가속이 오르는 속도는 출발 저크로 차량 ECU 가 제한한다.
      launch_jerk = controls.LoC.scc_launch_jerk() if hasattr(controls.LoC, 'scc_launch_jerk') else None
      if launch_jerk is not None:
        jerk_upper = clip(launch_jerk, 0.5, jerk_limit)

    # Community safety now follows the physical SCC MAIN state independently
    # of stock ACC engagement. Start replacing SCC messages as soon as
    # openpilot longitudinal control is configured, matching apilot-c2.
    if self.longcontrol and (CS.scc_bus or not self.scc_live):

      if self.frame % 2 == 0:
        controls.scc_stop_request = scc_stop_request

        set_speed = hud_control.setSpeed
        min_set_speed = controls.cruise_helper.cruise_speed_min * CV.KPH_TO_MS
        if not (min_set_speed < set_speed < 255 * CV.KPH_TO_MS):
          set_speed = max(CS.out.vEgo, min_set_speed)
        set_speed *= CV.MS_TO_MPH if CS.is_set_speed_in_mph else CV.MS_TO_KPH

        requested_accel = actuators.accel if (CC.longActive or stopping or soft_hold_scc) else 0.0
        # LongControl already applies every positive cap with its jerk limit.
        apply_accel = clip(requested_accel,
                           CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX)

        # Panda rejects any nonzero SCC12 request while the driver brake is
        # applied (brake_pressed_prev). A rejected frame breaks openpilot's
        # ownership of the SCC12 stream for one cycle and lets the stock SCC12
        # back onto the bus, which the cluster reports as a fault chime. Zero
        # the request here so the frame is always accepted.
        if CS.out.brakePressed and not soft_hold_scc:
          apply_accel = 0.0

        self.accel = apply_accel

        controls.apply_accel = apply_accel
        aReqValue = CS.scc12["aReqValue"]
        controls.aReqValue = aReqValue

        if aReqValue < controls.aReqValueMin:
          controls.aReqValueMin = controls.aReqValue

        if aReqValue > controls.aReqValueMax:
          controls.aReqValueMax = controls.aReqValue

        lead = controls.cruise_helper.get_lead(controls.sm)
        lead_distance = float(lead.dRel) if lead is not None else 0.0
        lead_relative_speed = float(lead.vRel) if lead is not None else 0.0

        if self.scc12_cnt < 0:
          self.scc12_cnt = CS.scc12["CR_VSM_Alive"] if not CS.no_radar else 0

        self.scc12_cnt += 1
        self.scc12_cnt %= 0xF

        can_sends.append(create_scc12(self.packer, apply_accel, CC.enabled, self.scc12_cnt, self.scc_live, CS.scc12,
                                      CS.out.gasPressed, CS.out.brakePressed and not soft_hold_scc,
                                      scc_stop_request,
                                      self.car_fingerprint, long_active=CC.longActive,
                                      soft_hold_active=soft_hold_scc))

        can_sends.append(create_scc11(self.packer, self.frame, CC.enabled, set_speed, hud_control.leadVisible, self.scc_live, CS.scc11,
                       controls.cruise_helper.active_cam, soft_hold=soft_hold and CC.longActive,
                       cruise_gap=controls.cruise_helper.long_cruise_gap,
                       lead_distance=lead_distance, lead_relative_speed=lead_relative_speed))

        if self.frame % 20 == 0 and CS.has_scc13:
          can_sends.append(create_scc13(self.packer, CS.scc13))

        if CS.has_scc14:
          acc_standstill = scc_stop_request

          # Comfort bands stay 0 like stock openpilot: LongControl alone
          # shapes the request, so the ECU should not add its own tolerance.
          cb_upper = cb_lower = 0.0

          if lead is not None:
            d = lead.dRel
            # aPilot C2 ObjGap scale. ObjGap2 intentionally remains untouched.
            obj_gap = 2 if d < 25 else 3 if d < 40 else 4 if d < 70 else 5
          else:
            obj_gap = 0

          can_sends.append(
            create_scc14(self.packer, CC.enabled, CS.out.vEgo, acc_standstill, apply_accel, CS.out.gasPressed,
                         obj_gap, CS.scc14, jerk_upper, jerk_lower, cb_upper, cb_lower,
                         long_active=CC.longActive, brakepressed=CS.out.brakePressed,
                         soft_hold_active=soft_hold_scc))
    else:
      self.scc12_cnt = -1
      controls.scc_stop_request = False
