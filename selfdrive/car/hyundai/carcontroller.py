from random import randint

from cereal import car, log
from common.realtime import DT_CTRL
from common.numpy_fast import clip
from selfdrive.car import apply_driver_steer_torque_limits
from selfdrive.car.hyundai.hyundaican import create_lkas11, create_clu11, \
  create_scc11, create_scc12, create_scc13, create_scc14, \
  create_mdps12, create_lfahda_mfc, create_hda_mfc
from selfdrive.car.hyundai.scc_smoother import SccSmoother
from selfdrive.car.hyundai.mcan_hud import McanHud
from selfdrive.car.hyundai.cgw_blinker import CgwBlinker
from selfdrive.car.hyundai.values import Buttons, FEATURES, CarControllerParams
from selfdrive.car.hyundai.cruise_buttons import button_pressed_in_samples
from opendbc.can.packer import CANPacker
from common.conversions import Conversions as CV
from common.params import Params
from selfdrive.controls.lib.longcontrol import LongCtrlState
from selfdrive.road_speed_limiter import road_speed_limiter_get_active

VisualAlert = car.CarControl.HUDControl.VisualAlert
LaneChangeState = log.LateralPlan.LaneChangeState

# Match aPilot c2-master: the EPS faults if torque is requested above about
# 90 degrees for too long. Keep the request valid for 89 frames, then clear
# the actuation bit and set ToiFlt for two consecutive frames.
MAX_ANGLE = 85
MAX_ANGLE_FRAMES = 89
MAX_ANGLE_CONSECUTIVE_FRAMES = 2


def should_request_scc_standstill(stopping, soft_hold_scc, car_standstill, v_ego, pre_release=False,
                                  latched=False):
  """Assert Hyundai StopReq only after the vehicle has actually stopped.

  pre_release (EARLY HOLD RELAX): LongControl saw the stopped lead start to
  move, so StopReq is dropped early while the negative brake request stays, to
  let the SCC begin leaving its standstill hold before the launch command.
  Soft hold (driver brake) always keeps StopReq.

  latched: StopReq was already asserted in this stopping phase. Keep it until
  stopping ends or pre_release, so a creep hovering around 0.1 m/s does not
  toggle it. On 2026-10-26 it toggled four times in 1.2 s; the SCC released the
  brake between toggles (+0.3 m/s2 forward) and then grabbed it again.
  """
  actual_standstill = car_standstill or v_ego < 0.1
  if soft_hold_scc:
    return bool(actual_standstill)
  return bool(stopping and (actual_standstill or latched) and not pre_release)


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

    self.longcontrol = CP.openpilotLongitudinalControl
    self.scc_live = not CP.radarOffCan

    param = Params()

    self.mad_mode_enabled = param.get_bool('MadModeEnabled')
    self.haptic_feedback_speed_camera = param.get_bool('HapticFeedbackWhenSpeedCamera')
    self.op_params = param

    self.scc_smoother = SccSmoother()
    self.soft_hold_mode = int(clip(param.get_int("SoftHoldMode"), 0, 2))
    # Naver TBT on the stock HUD over MM-CAN (bus 1). The panda only opens bus 1
    # when the same param was in the safety config at startup (interface.py).
    self.mcan_hud = McanHud() if param.get_bool("McanHudTbt") else None
    # CGW1 turn-signal request (cgw_blinker.py). Panda only echoes it when CgwBlinker was on at startup.
    self.cgw_blinker = CgwBlinker() if param.get_bool("CgwBlinker") else None
    self.cgw_blinker_test = 0
    self.scc_stop_latched = False
    self.prev_active_cam = False
    self.active_cam_timer = 0
    self.last_active_cam_frame = 0

    self.angle_limit_counter = 0
    self.max_angle_frames = MAX_ANGLE_FRAMES
    self.steer_delta_up = 3
    self.steer_delta_down = 7


  def update(self, CC, CS, controls):
    actuators = CC.actuators
    hud_control = CC.hudControl
    pcm_cancel_cmd = CC.cruiseControl.cancel

    # Steering Torque
    if self.frame % 100 == 0:
      self.max_angle_frames = int(clip(self.op_params.get_int("MaxAngleFrames"), 80, 100))
      self.steer_delta_up = int(clip(self.op_params.get_int("SteerDeltaUp"), 1, 20))
      self.steer_delta_down = int(clip(self.op_params.get_int("SteerDeltaDown"), 1, 20))

    new_steer = int(round(actuators.steer * self.params.STEER_MAX))
    self.params.STEER_DELTA_UP = self.steer_delta_up
    self.params.STEER_DELTA_DOWN = self.steer_delta_down
    apply_steer = apply_driver_steer_torque_limits(new_steer, self.apply_steer_last, CS.out.steeringTorque, self.params)

    # disable when temp fault is active, or below LKA minimum speed
    lkas_active = CC.latActive

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

    if lkas_active and abs(CS.out.steeringAngleDeg) >= MAX_ANGLE:
      self.angle_limit_counter += 1
    else:
      self.angle_limit_counter = 0

    torque_fault = lkas_active and self.angle_limit_counter > self.max_angle_frames
    lkas_active = lkas_active and not torque_fault
    # the steering request actually sent to the MDPS (interface.py MDPS fault display)
    self.lkas_request_sent = lkas_active

    if self.angle_limit_counter >= self.max_angle_frames + MAX_ANGLE_CONSECUTIVE_FRAMES:
      self.angle_limit_counter = 0


    can_sends = []
    can_sends.append(create_lkas11(self.packer, self.frame, self.car_fingerprint, apply_steer, lkas_active,
                                   CS.lkas11, sys_warning, sys_state, CC.enabled, hud_control.leftLaneVisible, hud_control.rightLaneVisible,
                                   left_lane_warning, right_lane_warning, 0, False, torque_fault))

    if CS.mdps_bus or CS.scc_bus == 1:  # send lkas11 bus 1 if mdps or scc is on bus 1
      can_sends.append(create_lkas11(self.packer, self.frame, self.car_fingerprint, apply_steer, lkas_active,
                                     CS.lkas11, sys_warning, sys_state, CC.enabled, hud_control.leftLaneVisible, hud_control.rightLaneVisible,
                                     left_lane_warning, right_lane_warning, 1, False, torque_fault))

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

    if self.mcan_hud is not None:
      can_sends.extend(self.mcan_hud.update(self.frame, controls.cruise_helper.navigation_route.state,
                                            CS.out.vEgo))

    if self.cgw_blinker is not None:
      can_sends.extend(self.update_cgw_blinker(CC, CS, controls))

    new_actuators = actuators.copy()
    new_actuators.steer = apply_steer / self.params.STEER_MAX
    new_actuators.accel = self.accel

    self.frame += 1
    return new_actuators, can_sends

  def update_cgw_blinker(self, CC, CS, controls):
    if self.frame % 20 == 0:
      self.cgw_blinker_test = self.op_params.get_int("CgwBlinkerTest")
    parked = (CS.out.gearShifter == car.CarState.GearShifter.park and CS.out.standstill and
              not (CC.enabled or CC.latActive or CC.longActive))
    lane_dir = 0
    if CC.latActive and CS.out.gearShifter == car.CarState.GearShifter.drive:
      plan = controls.sm['lateralPlan']
      if plan.laneChangeState != LaneChangeState.off:
        lane_dir = int(plan.laneChangeDirection)  # 1 left, 2 right
    return self.cgw_blinker.update(self.frame, self.cgw_blinker_test, parked, lane_dir,
                                   CS.cgw_hazard, CS.cgw_lever_left, CS.cgw_lever_right)

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
      stopping, soft_hold_scc, CS.out.standstill, CS.out.vEgo, pre_release,
      latched=self.scc_stop_latched)
    self.scc_stop_latched = scc_stop_request and stopping and not pre_release

    # apilot-c2: MPC/PID chooses acceleration once; SCC14 is the sole
    # command-side jerk owner. Follow planned jerk in normal driving and keep
    # the dedicated stop/launch bounds only at those state transitions.
    jerk_limit = 5.0
    planned_jerk = float(actuators.jerk)
    if jerk_stopping:
      jerk_upper = 0.5
      jerk_lower = jerk_limit
    else:
      jerk_upper = min(max(0.5, planned_jerk * 2.0), jerk_limit)
      jerk_lower = min(max(1.0, -planned_jerk * 2.0), jerk_limit)
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

        driver_braking = CS.out.brakePressed and not soft_hold_scc
        requested_accel = actuators.accel if (CC.longActive or stopping or soft_hold_scc) else 0.0
        apply_accel = clip(requested_accel,
                           CarControllerParams.ACCEL_MIN, CarControllerParams.ACCEL_MAX)

        # Panda rejects any nonzero SCC12 request while the driver brake is
        # applied (brake_pressed_prev). A rejected frame breaks openpilot's
        # ownership of the SCC12 stream for one cycle and lets the stock SCC12
        # back onto the bus, which the cluster reports as a fault chime. Zero
        # the request here so the frame is always accepted.
        if driver_braking:
          apply_accel = 0.0

        self.accel = apply_accel

        controls.apply_accel = apply_accel
        aReqValue = CS.scc12["aReqValue"]
        controls.aReqValue = aReqValue

        controls.aReqValueMin = min(controls.aReqValueMin, aReqValue)
        controls.aReqValueMax = max(controls.aReqValueMax, aReqValue)

        lead = controls.cruise_helper.get_lead(controls.sm)
        lead_distance = float(lead.dRel) if lead is not None else 0.0
        lead_relative_speed = float(lead.vRel) if lead is not None else 0.0

        if self.scc12_cnt < 0:
          self.scc12_cnt = CS.scc12["CR_VSM_Alive"] if not CS.no_radar else 0

        self.scc12_cnt += 1
        self.scc12_cnt %= 0xF

        can_sends.append(create_scc12(self.packer, apply_accel, CC.enabled, self.scc12_cnt, self.scc_live, CS.scc12,
                                      CS.out.gasPressed, driver_braking,
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
          cb_upper = clip(0.9 + apply_accel * 0.2, 0.0, 1.2)
          cb_lower = clip(0.8 + apply_accel * 0.2, 0.0, 1.2)

          if lead is not None:
            d = lead.dRel
            # aPilot C2 ObjGap scale. ObjGap2 intentionally remains untouched.
            obj_gap = 2 if d < 25 else 3 if d < 40 else 4 if d < 70 else 5
          else:
            obj_gap = 0

          can_sends.append(
            create_scc14(self.packer, CC.enabled, CS.out.vEgo, scc_stop_request, apply_accel, CS.out.gasPressed,
                         obj_gap, CS.scc14, jerk_upper, jerk_lower, cb_upper, cb_lower,
                         long_active=CC.longActive, brakepressed=CS.out.brakePressed,
                         soft_hold_active=soft_hold_scc))
    else:
      self.scc12_cnt = -1
      controls.scc_stop_request = False
