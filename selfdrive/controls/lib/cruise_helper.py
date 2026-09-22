import numpy as np

from cereal import car, log
from common.conversions import Conversions as CV
from common.numpy_fast import clip, interp
from common.params import Params, put_nonblocking
from common.realtime import DT_CTRL, sec_since_boot
from selfdrive.car.hyundai.values import Buttons
from selfdrive.controls.lib.navigation_route import NavigationRouteData
from selfdrive.controls.lib.vision_curve_speed import VisionCurveSpeed, UNLIMITED_SPEED
from selfdrive.controls.lib.drive_helpers import V_CRUISE_MAX, V_CRUISE_MIN, V_CRUISE_DELTA_KM, V_CRUISE_DELTA_MI
from selfdrive.controls.lib.gap_sync import select_physical_gap, select_software_gap
from selfdrive.controls.lib.lead_following import APPROACH_ACCEL_LIMIT_FALL, get_follow_approach_limit
from selfdrive.controls.lib.longitudinal_limits import (CRUISE_MAX_VAL_DEFAULTS,
                                                        CRUISE_MAX_VAL_KEYS,
                                                        get_auto_speed_up_target,
                                                        apply_no_lead_cruise_accel_limit,
                                                        apply_cruise_max_limit,
                                                        get_cruise_max_accel,
                                                        get_no_lead_cruise_accel_cap,
                                                        transition_follow_accel_limit,
                                                        select_auto_driving_mode)
from selfdrive.road_speed_limiter import get_road_speed_limiter


SYNC_MARGIN = 3.0
NAVI_DISTANCE_HOLD_TIME = 1.0
NAVI_DISTANCE_MAX_DT = 0.2
MIN_SET_SPEED_KPH = V_CRUISE_MIN
MAX_SET_SPEED_KPH = V_CRUISE_MAX

# 핸들 차간거리(GAP) 버튼 길게 누르기 = 내비 앱 전환(티맵↔네이버).
# 짧게 누르면 종전대로 차간 단계가 바뀌고, 이 시간을 넘기면 전환만 하고
# 차간 변경은 취소한다. 100 Hz 기준 프레임 수.
NAV_TOGGLE_HOLD_FRAMES = 150   # 1.5 s
ButtonType = car.CarState.ButtonEvent.Type
EventName = car.CarEvent.EventName
XState = log.LongitudinalPlan.XState


class CruiseHelper:
  """Owns cruise policy; Hyundai CAN transmission stays in SccSmoother."""

  def __init__(self, params=None):
    self.params = params or Params()
    self.param_read_counter = 0
    self.param_read_group = 0

    self.button_count = 0
    self.button_long_pressed = False
    self.button_prev = ButtonType.unknown

    self.is_cruise_enabled = False
    # aPilot C2 separates lateral engagement from longitudinal readiness.
    # CANCEL/brake can pause longitudinal control while steering remains enabled.
    self.auto_cruise_control = False
    self.long_active_user = 0
    self.user_cruise_paused = False
    self.v_cruise_kph_backup = 0.0
    self.prev_brake_pressed = False
    self.gas_pressed_count = 0
    self.pre_gas_pressed_max = 0.0
    self.gas_tap_cruise_active = False
    self.gas_tap_set_speed_kph = 0.0
    self.gas_pressed_frame = 0
    self.slow_speed_frame_count = 0
    self.x_state = XState.cruise
    self.x_stop = 0.0
    self.traffic_state = 0
    self.traffic_state_prev = 0
    self.d_rel = 0.0
    self.v_rel = 0.0
    self.lead_car_speed_kph = 0.0
    self.long_cruise_gap = 4
    self.gap_param_initialized = False
    self.gap_pressed = False
    self.gap_hold_frames = 0
    self.nav_toggle_done = False
    self.init_driving_mode = 3
    self.my_driving_mode = 3
    self.last_mode_param = 3
    self.driving_mode_index = 0.0
    self.safe_mode_base_factor = 0.8
    self.my_safe_mode_factor = 1.0
    self.my_eco_mode_factor = 0.8
    self.cruise_max_vals = list(CRUISE_MAX_VAL_DEFAULTS)
    self.no_lead_cruise_accel_factor = 0.65
    self.no_lead_cruise_jerk_limit = 0.25
    self.last_apply_accel = 0.0
    self.follow_accel_limit = None
    self.current_set_speed_kph = 0.0

    self.target_speed = 0.0
    self.max_speed_clu = 0.0
    self.curve_speed_ms = UNLIMITED_SPEED
    self.vision_curve_speed = VisionCurveSpeed()
    self.curve_update_frame = None
    self.apply_source = ""
    self.active_cam = False
    self.slowing_down = False
    self.slowing_down_alert = False
    self.slowing_down_sound_alert = False
    self.slowing_down_for_bump = False
    self.stock_weight = 0.0

    # c3-wip style navigation distance tracking. This branch has no
    # selfdriveState.distanceTraveled, so integrate actual loop time and vEgo.
    self.navi_distance_time = sec_since_boot()
    self.cam_dist_est = 0.0
    self.cam_raw_dist = 0.0
    self.cam_limit_est = 0.0
    self.cam_type_est = -1
    self.cam_last_valid_time = 0.0
    self.cam_passed = False
    self.section_dist_est = 0.0
    self.section_raw_dist = 0.0
    self.section_limit_est = 0.0
    self.section_last_valid_time = 0.0
    self.section_passed = False

    self.navigation_route = NavigationRouteData()
    self.nav_app_selected = None
    self.last_road_limit_speed = 0.0
    self.pause_auto_speed_up = False

    self.read_params()

  def read_cruise_params(self):
    self.is_metric = self.params.get_bool("IsMetric")
    self.speed_conv_to_ms = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS
    self.speed_conv_to_clu = CV.MS_TO_KPH if self.is_metric else CV.MS_TO_MPH

    self.cruise_speed_min = int(clip(self.params.get_int("CruiseSpeedMin"),
                                     MIN_SET_SPEED_KPH, MAX_SET_SPEED_KPH))
    self.min_set_speed_clu = self.kph_to_clu(self.cruise_speed_min)
    self.max_set_speed_clu = self.kph_to_clu(MAX_SET_SPEED_KPH)
    self.speed_from_pcm = int(clip(self.params.get_int("SpeedFromPCM"), 1, 2))
    self.cruise_button_mode = int(clip(self.params.get_int("CruiseButtonMode"), 0, 3))
    self.cruise_speed_unit = int(clip(self.params.get_int("CruiseSpeedUnit"), 1, 20))
    self.cruise_speed_unit_basic = int(clip(self.params.get_int("CruiseSpeedUnitBasic"), 1, 10))
    self.cruise_button_long_delay = int(clip(self.params.get_int("CruiseButtonLongDelay"), 30, 150))
    # c3-wip keeps the raw values and resolves them at use time so an unset
    # CruiseSpeed1 can fall back to the live road speed limit.
    self.cruise_speed_table_raw = [self.params.get_int(f"CruiseSpeed{i}") for i in range(1, 6)]
    self.cruise_speed_table = self.build_cruise_speed_table()
    self.sync_set_speed_while_gas_pressed = self.params.get_bool("SccSmootherSyncGasPressed")
    self.cruise_max_vals = []
    for key, default in zip(CRUISE_MAX_VAL_KEYS, CRUISE_MAX_VAL_DEFAULTS):
      raw = self.params.get_int(key)
      self.cruise_max_vals.append(float(raw * 0.01 if raw > 0 else default))
    no_lead_factor = self.params.get_int("NoLeadCruiseAccelFactor")
    self.no_lead_cruise_accel_factor = float(clip(
      (no_lead_factor if no_lead_factor > 0 else 65) * 0.01, 0.30, 1.0))
    no_lead_jerk = self.params.get_int("NoLeadCruiseJerkLimit")
    self.no_lead_cruise_jerk_limit = float(clip(
      (no_lead_jerk if no_lead_jerk > 0 else 25) * 0.01, 0.05, 1.0))

  def read_curve_params(self):
    self.turn_vision_control = self.params.get_bool("TurnVisionControl")
    curve_factor = self.params.get_int("AutoCurveSpeedFactor")
    curve_lower = self.params.get_int("AutoCurveSpeedLowerLimit")
    map_factor = self.params.get_int("MapTurnSpeedFactor")
    navi_decel = self.params.get_int("AutoNaviSpeedDecelRate")
    self.auto_curve_speed_factor = float(clip(curve_factor if curve_factor > 0 else 120, 50, 300)) * 0.01
    self.auto_curve_speed_lower_limit = float(clip(curve_lower if curve_lower > 0 else 30, 5, 80))
    self.map_turn_speed_factor = float(clip(map_factor if map_factor > 0 else 90, 50, 150)) * 0.01
    self.auto_navi_speed_decel_rate = float(clip(navi_decel if navi_decel > 0 else 80, 10, 300)) * 0.01
    # 커브(경로 곡률) 감속 세기를 카메라와 분리. 0(미설정)이면 카메라 값을 따른다.
    curve_decel = self.params.get_int("AutoCurveSpeedDecelRate")
    self.auto_curve_speed_decel_rate = (float(clip(curve_decel, 10, 300)) * 0.01
                                        if curve_decel > 0 else self.auto_navi_speed_decel_rate)

  def read_pedal_params(self):
    # C2 pedal-resume settings. Existing branch keys are used so no unregistered
    # Params access can crash controlsd.
    self.auto_resume_from_gas_speed = float(clip(self.params.get_int("AutoGasTokSpeed"), 5, 160))
    self.auto_gas_cancel_speed = float(clip(self.params.get_int("AutoGasCancelSpeed"), 0, 160))
    self.auto_gas_tap_speed_increment = int(clip(self.params.get_int("AutoGasTapSpeedIncrement"), 0, 30))
    self.auto_gas_resume_guard = self.params.get_bool("AutoGasResumeGuard")
    self.auto_resume_from_gas = int(clip(self.params.get_int("AutoResumeFromGas"), 0, 2))
    self.auto_resume_from_gas_speed_mode = int(clip(self.params.get_int("AutoResumeFromGasSpeedMode"), 0, 3))
    self.auto_resume_from_brake_release = self.params.get_bool("AutoResumeFromBrakeRelease")
    self.auto_resume_from_brake_car_speed = float(clip(self.params.get_int("AutoResumeFromBrakeCarSpeed"), 0, 160))
    self.auto_resume_from_brake_release_dist = float(clip(self.params.get_int("AutoResumeFromBrakeReleaseDist"), 0, 100))

  def read_navigation_params(self):
    self.auto_speed_up_ratio = float(self.params.get_int("AutoSpeedUptoRoadSpeedLimit")) * 0.01
    self.auto_road_speed_adjust = float(clip(self.params.get_int("AutoRoadSpeedAdjust"), -100, 100)) * 0.01
    self.auto_road_speed_limit_offset = float(clip(self.params.get_int("AutoRoadSpeedLimitOffset"), -30, 30))
    ctrl_end = self.params.get_int("AutoNaviSpeedCtrlEnd")
    bump_time = self.params.get_int("AutoNaviSpeedBumpTime")
    bump_speed = self.params.get_int("AutoNaviSpeedBumpSpeed")
    safety_factor = self.params.get_int("AutoNaviSpeedSafetyFactor")
    release_dist = self.params.get_int("AutoNaviSpeedReleaseDist")
    # 카메라 이 거리(m) 앞에서 감속 유지를 끝내고 원래 속도로 복귀. 0 = 카메라를 지난 뒤.
    self.auto_navi_speed_release_dist = float(clip(release_dist, 0, 50))
    self.auto_navi_speed_ctrl_end = float(clip(ctrl_end if ctrl_end > 0 else 7, 3, 20))
    self.auto_navi_speed_bump_time = float(clip(bump_time if bump_time > 0 else 1, 1, 50))
    self.auto_navi_speed_bump_speed = float(clip(bump_speed if bump_speed > 0 else 35, 10, 100))
    self.auto_navi_speed_safety_factor = float(clip(safety_factor if safety_factor > 0 else 105, 80, 120)) * 0.01
    self.noo_enabled = self.params.get_bool("NavigationOnOpenpilot")
    # 0: all, 1: turn steering+speed, 2: lane preparation only,
    # 3: carrot-style speed only.
    self.noo_mode = int(clip(self.params.get_int("NooMode"), 0, 3))
    turn_speed = self.params.get_int("NooTurnSpeed")
    self.noo_turn_speed = float(clip(turn_speed if turn_speed > 0 else 20, 20, 80))
    self.noo_turn_end_time = float(clip(self.params.get_int("NooTurnEndTime"), 1, 20))

  def read_driving_mode_params(self, initialize=False):
    self.init_driving_mode = int(clip(self.params.get_int("InitMyDrivingMode"), 1, 5))
    mode = self.params.get_int("MyDrivingMode")
    if initialize:
      self.my_driving_mode = 3 if self.init_driving_mode == 5 else self.init_driving_mode
      # Keep the persisted value aligned with the effective mode. The onroad
      # button advances from controlsState; a stale Params value can otherwise
      # make the first tap rewrite the same value and appear unresponsive.
      self.last_mode_param = self.my_driving_mode
      if mode != self.my_driving_mode:
        self.params.put("MyDrivingMode", str(self.my_driving_mode))
    elif mode != self.last_mode_param and 1 <= mode <= 4:
      self.my_driving_mode = mode
      self.last_mode_param = mode
      self.driving_mode_index = -100.0
    self.safe_mode_base_factor = float(clip(self.params.get_int("MySafeModeFactor") * 0.01, 0.5, 1.0))
    eco_factor = self.params.get_int("MyEcoModeFactor")
    self.my_eco_mode_factor = float(clip((eco_factor if eco_factor > 0 else 80) * 0.01, 0.1, 0.95))
    self.update_safe_mode_factor()

  def read_params(self):
    # Read every group at construction. Runtime refreshes are staggered below
    # so controlsd never performs dozens of Params file reads in one 100 Hz
    # control iteration.
    self.read_cruise_params()
    self.read_curve_params()
    self.read_pedal_params()
    self.read_navigation_params()

    # PrevCruiseGap is the source of truth for openpilot longitudinal control.
    # Load it once so a delayed nonblocking write cannot bounce the live value.
    if not self.gap_param_initialized:
      saved_gap = self.params.get_int("PrevCruiseGap")
      self.long_cruise_gap = int(saved_gap) if 1 <= saved_gap <= 4 else 4
      self.gap_param_initialized = True

    self.read_driving_mode_params(initialize=True)

  def build_cruise_speed_table(self):
    # c3-wip behavior: CruiseSpeed1 left at 0 tracks the road speed limit
    # instead of collapsing to the minimum set speed. Entries that stay at or
    # below zero are dropped so RES never steps onto a placeholder value.
    raw_values = list(getattr(self, "cruise_speed_table_raw", []))
    resolved = []
    for index, raw in enumerate(raw_values):
      value = float(raw)
      if index == 0 and value <= 0.0:
        road_limit = float(self.last_road_limit_speed)
        if road_limit > 0.0:
          offset = float(getattr(self, "auto_road_speed_limit_offset", 0.0))
          safety = float(getattr(self, "auto_navi_speed_safety_factor", 1.0))
          value = road_limit * safety if offset < 0.0 else road_limit + offset
      if value <= 0.0:
        continue
      resolved.append(float(clip(value, self.cruise_speed_min, MAX_SET_SPEED_KPH)))
    return sorted(set(resolved))

  def _clear_speed_backup(self):
    # c3-wip treats the pre-brake speed as single use: once RES restores it, or
    # SET states a new intent, the stored value is dropped so a stale set speed
    # cannot reappear minutes later.
    self.v_cruise_kph_backup = 0.0

  def kph_to_clu(self, kph):
    return int(kph * CV.KPH_TO_MS * self.speed_conv_to_clu)

  @staticmethod
  def get_lead(sm):
    lead = sm['radarState'].leadOne
    return lead if lead.status else None

  def update_safe_mode_factor(self):
    if self.my_driving_mode == 1:
      self.my_safe_mode_factor = self.safe_mode_base_factor
    elif self.my_driving_mode == 2:
      self.my_safe_mode_factor = (1.0 + self.safe_mode_base_factor) / 2.0
    else:
      self.my_safe_mode_factor = 1.0

  def get_cruise_max_accel(self, v_ego):
    return get_cruise_max_accel(v_ego, self.cruise_max_vals, self.my_driving_mode,
                                self.my_eco_mode_factor, self.my_safe_mode_factor)

  def get_longitudinal_accel_limit(self, CS, sm, set_speed_kph):
    """Return the live positive limit shared by LongControl and SCC output."""
    cruise_max_accel = self.get_cruise_max_accel(CS.vEgo)
    speed_error_kph = max(0.0, float(set_speed_kph) - CS.vEgo * CV.MS_TO_KPH)
    no_lead_cap = get_no_lead_cruise_accel_cap(
      cruise_max_accel, speed_error_kph, self.no_lead_cruise_accel_factor)
    has_lead = sm['radarState'].leadOne.status or sm['radarState'].leadTwo.status
    target = cruise_max_accel if has_lead else no_lead_cap
    plan = sm['longitudinalPlan']
    comfort_valid = all(sm.valid[s] and sm.alive[s] for s in ('radarState', 'longitudinalPlan'))
    if comfort_valid and has_lead and plan.mpcMode == 0 and not (plan.onStop or plan.fcw):
      target, comfortable = get_follow_approach_limit(
        target, CS.vEgo, (sm['radarState'].leadOne, sm['radarState'].leadTwo),
        float(plan.desiredDistance))
      if comfortable and self.follow_accel_limit is not None:
        # Ease throttle lift, but never delay an actual MPC/PID braking request
        # or a lower user-configured acceleration ceiling.
        target = min(cruise_max_accel, max(target, self.follow_accel_limit - APPROACH_ACCEL_LIMIT_FALL * DT_CTRL))
    # An MPC source label change is not a new lead or a new acceleration
    # demand. Preserve the allowance across lead/cruise switches; re-anchoring
    # it to a small/coasting output repeatedly delayed normal gap recovery.
    # Actual lead, distance and user-limit changes still set the target above.
    self.follow_accel_limit = transition_follow_accel_limit(
      target, self.follow_accel_limit, CS.vEgo, DT_CTRL)
    return self.follow_accel_limit

  def _resume_longitudinal(self, controls, CS, active_mode=1):
    if self.long_active_user <= 0:
      controls.LoC.reset(v_pid=CS.vEgo)
    self.long_active_user = active_mode
    self.user_cruise_paused = False
    self.auto_cruise_control = True

  def pause_longitudinal(self, controls, user_cancel=False):
    if self.long_active_user > 0 and self.cruise_speed_min <= controls.v_cruise_kph <= MAX_SET_SPEED_KPH:
      self.v_cruise_kph_backup = controls.v_cruise_kph
    self.long_active_user = 0 if user_cancel else -2
    if user_cancel:
      self.user_cruise_paused = True
      self.auto_cruise_control = False

  def _resume_guard_ok(self, CS):
    if abs(CS.steeringAngleDeg) >= 20.0:
      return False
    if self.auto_gas_resume_guard:
      if CS.leftBlinker or CS.rightBlinker:
        return False
      danger_dist = max(5.0, CS.vEgo * 0.8)
      if 0.0 < self.d_rel < danger_dist:
        return False
    return True

  def _select_resume_speed(self, controls, CS):
    current_kph = float(clip(CS.vEgoCluster * CV.MS_TO_KPH,
                             self.cruise_speed_min, MAX_SET_SPEED_KPH))
    backup_kph = self.v_cruise_kph_backup
    if not self.cruise_speed_min <= backup_kph <= MAX_SET_SPEED_KPH:
      backup_kph = current_kph

    if self.auto_resume_from_gas_speed_mode == 1:
      selected = backup_kph
    elif self.auto_resume_from_gas_speed_mode == 2:
      selected = backup_kph if 0.0 < self.d_rel < 60.0 and self.lead_car_speed_kph >= current_kph else current_kph
    elif self.auto_resume_from_gas_speed_mode == 3:
      selected = backup_kph if self.x_stop > 60.0 and self.gas_pressed_count * DT_CTRL > 1.0 else current_kph
    else:
      selected = current_kph
    controls.v_cruise_kph = float(clip(selected, self.cruise_speed_min, MAX_SET_SPEED_KPH))
    if selected == backup_kph:
      self._clear_speed_backup()

  def _brake_release_resume(self, controls, CS):
    if not self.auto_cruise_control:
      return
    v_ego_kph = CS.vEgoCluster * CV.MS_TO_KPH

    # C2 soft-hold release path.
    if v_ego_kph < 5.0 and self.x_state == XState.softHold:
      self._resume_longitudinal(controls, CS, 3)
      return

    if not self.auto_resume_from_brake_release or abs(CS.steeringAngleDeg) >= 20.0:
      return

    gas_time = (self.param_read_counter - self.gas_pressed_frame) * DT_CTRL
    # c3-wip gates this on AutoGasTokSpeed rather than a hardcoded 20 km/h, so
    # the UI value covers both pedal-resume paths.
    if v_ego_kph < self.auto_resume_from_gas_speed:
      gas_wait = 5.0 if self.slow_speed_frame_count * DT_CTRL > 10.0 else 0.0
      if gas_time < gas_wait:
        return
      if 0.0 < self.d_rel < 20.0 and (CS.leftBlinker or CS.rightBlinker):
        return
      if 0.0 < self.d_rel <= max(10.0, self.auto_resume_from_brake_release_dist):
        self._resume_longitudinal(controls, CS, 3)
      elif self.d_rel <= 0.0 and self.traffic_state == 1 and not (CS.leftBlinker or CS.rightBlinker):
        self._resume_longitudinal(controls, CS, 3)
    elif self.d_rel > self.auto_resume_from_brake_release_dist > 0.0:
      # Brake release now honors AutoResumeFromGasSpeedMode like the gas path
      # instead of always clamping the set speed down to the current speed.
      self._select_resume_speed(controls, CS)
      self._resume_longitudinal(controls, CS, 3)
    elif self.d_rel <= 0.0 and v_ego_kph >= self.auto_resume_from_brake_car_speed > 0.0:
      self._select_resume_speed(controls, CS)
      self._resume_longitudinal(controls, CS, 3)

  def _update_pedal_cruise(self, controls, CS):
    self.traffic_state_prev = self.traffic_state
    try:
      plan = controls.sm['longitudinalPlan']
      self.x_state = plan.xState
      self.x_stop = float(getattr(plan, 'xStop', 0.0))
      self.traffic_state = int(getattr(plan, 'trafficState', 0)) % 100
    except Exception:
      self.x_state = XState.cruise
      self.x_stop = 0.0
      self.traffic_state = 0

    # C2-style traffic events: publish only on a state edge so there is no
    # per-frame UI work. A pedal-assisted departure is a driver action, not a
    # detected green signal, and must not show the automatic-depart message.
    if controls.enabled and self.long_active_user > 0:
      if self.traffic_state == 1 and self.traffic_state_prev != 1:
        controls.events.add(EventName.trafficStopping)
      elif self.traffic_state_prev == 1 and self.traffic_state == 2 and \
           not CS.gasPressed and not CS.brakePressed:
        controls.events.add(EventName.trafficSignGreen)

    lead = self.get_lead(controls.sm)
    self.d_rel = lead.dRel if lead is not None else 0.0
    self.v_rel = lead.vRel if lead is not None else 0.0
    v_ego_kph = CS.vEgoCluster * CV.MS_TO_KPH
    self.lead_car_speed_kph = v_ego_kph + self.v_rel * CV.MS_TO_KPH

    brake_pressed = CS.brakePressed or bool(getattr(CS, 'regenBraking', False))
    if not controls.enabled:
      self.long_active_user = 0
      self.gas_pressed_count = 0
      self.pre_gas_pressed_max = 0.0
      self.gas_tap_cruise_active = False
      self.gas_tap_set_speed_kph = 0.0
    elif brake_pressed:
      # Match aPilot C2 pedal priority: brake input owns this control cycle.
      if not self.prev_brake_pressed:
        self.pause_longitudinal(controls)
      self.gas_tap_cruise_active = False
      self.gas_tap_set_speed_kph = 0.0
    elif CS.gasPressed:
      # Gas input is evaluated before either pedal-release path. This prevents
      # brake release from resuming longitudinal control and the gas path from
      # immediately pausing it again in the same low-speed control cycle.
      if self.gas_pressed_count == 0:
        # A traffic-stop pedal departure is not a cruise-speed command. Also
        # require longitudinal control to remain active through release below.
        self.gas_tap_cruise_active = self.long_active_user > 0 and self.traffic_state != 1
        self.gas_tap_set_speed_kph = float(controls.v_cruise_kph)
      self.gas_pressed_count += 1
      self.gas_pressed_frame = self.param_read_counter
      self.pre_gas_pressed_max = max(self.pre_gas_pressed_max, float(CS.gas))

      if self.long_active_user <= 0 and self.auto_resume_from_gas > 0 and self.auto_cruise_control and \
         self.traffic_state != 1 and self._resume_guard_ok(CS) and \
         (v_ego_kph >= self.auto_resume_from_gas_speed or CS.gas >= 0.6):
        self._select_resume_speed(controls, CS)
        self._resume_longitudinal(controls, CS, 3)
      elif self.long_active_user > 0 and 0.0 < self.auto_gas_cancel_speed and v_ego_kph < self.auto_gas_cancel_speed:
        self.pause_longitudinal(controls)

      if self.auto_resume_from_gas_speed < v_ego_kph and v_ego_kph > controls.v_cruise_kph:
        controls.v_cruise_kph = float(clip(v_ego_kph, self.cruise_speed_min, MAX_SET_SPEED_KPH))
    elif self.gas_pressed_count > 0:
      # Match aPilot C2: process gas release before brake release.
      quick_release = self.gas_pressed_count * DT_CTRL < 0.6 and self.pre_gas_pressed_max > 0.03
      if quick_release and self.gas_tap_cruise_active and self.long_active_user > 0 and \
         self.auto_gas_tap_speed_increment > 0:
        controls.v_cruise_kph = float(clip(
          self.gas_tap_set_speed_kph + self.auto_gas_tap_speed_increment,
          self.cruise_speed_min, MAX_SET_SPEED_KPH))
      elif quick_release and self.auto_resume_from_gas > 1 and self.long_active_user <= 0 and \
         self.auto_cruise_control and v_ego_kph >= self.auto_resume_from_gas_speed and self._resume_guard_ok(CS):
        self._select_resume_speed(controls, CS)
        self._resume_longitudinal(controls, CS, 3)
      self.gas_pressed_count = 0
      self.pre_gas_pressed_max = 0.0
      self.gas_tap_cruise_active = False
      self.gas_tap_set_speed_kph = 0.0
    elif self.prev_brake_pressed:
      # Brake-release resume runs only when neither gas press nor gas release
      # was handled, matching aPilot C2's mutually exclusive elif chain.
      self._brake_release_resume(controls, CS)

    self.prev_brake_pressed = brake_pressed
    if v_ego_kph < 20.0:
      self.slow_speed_frame_count += 1
    else:
      self.slow_speed_frame_count = 0

  def update_driving_mode(self, CS, sm):
    lead = self.get_lead(sm)
    accel_index = interp(CS.aEgo, [-3.0, -1.0, 0.0, 1.0, 3.0], [100.0, 0.0, 0.0, 0.0, 100.0])
    velocity_index = interp(CS.vEgo * CV.MS_TO_KPH, [0.0, 5.0, 50.0], [100.0, 80.0, 0.0])
    total_index = accel_index * 3.0 + velocity_index if lead is not None and 0.0 < lead.dRel < 50.0 else 0.0
    self.driving_mode_index = self.driving_mode_index * 0.999 + total_index * 0.001

    auto_mode = select_auto_driving_mode(self.init_driving_mode, self.my_driving_mode,
                                         self.driving_mode_index)
    if auto_mode != self.my_driving_mode:
      self.my_driving_mode = auto_mode
      # Keep the persisted mode synchronized with AUTO. Otherwise a UI tap
      # can write the same stale value already stored in Params and the change
      # is invisible to the change detector in read_driving_mode_params().
      self.last_mode_param = auto_mode
      # AUTO transitions are rare; a synchronous write prevents a delayed
      # background write from overwriting a nearly simultaneous UI selection.
      self.params.put("MyDrivingMode", str(auto_mode))
    self.update_safe_mode_factor()

  def update_button_events(self, controls, CS, longcontrol):
    self.update_cruise_speed(controls, CS, longcontrol)
    self.sync_physical_gap(controls, CS, longcontrol)

    # Pause on the CANCEL press, not its release. A held button must never
    # leave longitudinal control active while waiting for the release frame.
    # Lateral control remains engaged until cruise MAIN turns off.
    if any(event.type == ButtonType.cancel and event.pressed for event in CS.buttonEvents):
      self.pause_longitudinal(controls, user_cancel=True)
      self.button_count = 0
      self.button_long_pressed = False
      self.button_prev = ButtonType.unknown
      return

    # RES/SET controls longitudinal activation while lateral control remains
    # engaged, including after brake/cancel/standstill.
    if not controls.enabled:
      self.button_count = 0
      self.button_long_pressed = False
      self.button_prev = ButtonType.unknown
      return

    if self.button_count > 0:
      self.button_count += 1

    for event in CS.buttonEvents:
      if event.type not in (ButtonType.accelCruise, ButtonType.decelCruise):
        continue

      if event.pressed:
        # Recover deterministically if a malformed/missing release left a
        # different button active. Normal direct transitions are emitted as
        # release-old then press-new by Hyundai CarInterface.
        if self.button_count == 0 or event.type != self.button_prev:
          self.button_count = 1
          self.button_long_pressed = False
          self.button_prev = event.type
      elif self.button_count > 0:
        if event.type != self.button_prev:
          # Never keep repeating the old direction after an unmatched release.
          self.button_count = 0
          self.button_long_pressed = False
          self.button_prev = ButtonType.unknown
          continue

        if self.long_active_user <= 0:
          current_kph = float(clip(CS.vEgoCluster * CV.MS_TO_KPH,
                                   self.cruise_speed_min, MAX_SET_SPEED_KPH))
          if event.type == ButtonType.accelCruise:
            controls.v_cruise_kph = max(current_kph, self.v_cruise_kph_backup,
                                        controls.v_cruise_kph if controls.v_cruise_kph <= MAX_SET_SPEED_KPH else 0.0)
          else:
            controls.v_cruise_kph = current_kph
          self._clear_speed_backup()
          self._resume_longitudinal(controls, CS, 1)
        elif not self.button_long_pressed:
          controls.v_cruise_kph = self.apply_button_speed(controls.v_cruise_kph, event.type, False, CS.vEgo)
          self._clear_speed_backup()
        self.button_count = 0
        self.button_long_pressed = False
        self.button_prev = ButtonType.unknown

    if self.button_count > self.cruise_button_long_delay:
      self.button_long_pressed = True
      if self.button_prev in (ButtonType.accelCruise, ButtonType.decelCruise):
        controls.v_cruise_kph = self.apply_button_speed(controls.v_cruise_kph, self.button_prev, True, CS.vEgo)
        self._clear_speed_backup()
        self._resume_longitudinal(controls, CS, 1)
        self.button_count %= self.cruise_button_long_delay

    if longcontrol:
      controls.v_cruise_cluster_kph = controls.v_cruise_kph

  def sync_physical_gap(self, controls, CS, longcontrol):
    # Hyundai SCC falls back to gap 4 as cruise disengages. Only persist a gap
    # after a completed short button press, never from a passive SCC value.
    gap_button_events = [event for event in CS.buttonEvents if event.type == ButtonType.gapAdjustCruise]
    short_gap_release = False
    navigation_gap_release = False

    # ── GAP 길게 누르기 → 내비 앱 전환 ──
    # carState(capnp)에는 현재 눌린 버튼 필드가 없으므로 buttonEvents 의
    # 누름/뗌으로 유지시간을 잰다. 짧은 누름인지 긴 누름인지 알기 전에는
    # PrevCruiseGap을 바꾸지 않는다.
    for event in gap_button_events:
      if event.pressed:
        self.gap_pressed = True
        self.gap_hold_frames = 0
        self.nav_toggle_done = False
      else:
        self.gap_pressed = False
        if self.nav_toggle_done:
          # 전환에 쓰인 입력이므로 차간 단계는 바꾸지 않는다.
          navigation_gap_release = True
          self.nav_toggle_done = False
        else:
          short_gap_release = True
    if self.gap_pressed:
      self.gap_hold_frames += 1
      if self.gap_hold_frames >= NAV_TOGGLE_HOLD_FRAMES and not self.nav_toggle_done:
        self.nav_toggle_done = True
        try:
          current = self.params.get_int("EonClusterHudNavApp")
          put_nonblocking("EonClusterHudNavApp", "1" if current == 2 else "2")
        except (TypeError, ValueError):
          pass

    if longcontrol:
      # The stock SCC can restart at gap 4 even when PrevCruiseGap is 2. Cycle
      # from the persisted value only after a completed short press. A long
      # press belongs exclusively to navigation selection.
      gap, changed = select_software_gap(self.long_cruise_gap, short_gap_release)
    else:
      # With stock SCC the physical GAP button really changes TauGapSet even
      # when its long press is also used for navigation. Reflect that actual
      # value in software on release so the HUD and persisted gap cannot drift
      # apart. Openpilot longitudinal control keeps its saved gap above.
      gap, changed = select_physical_gap(self.long_cruise_gap, CS.cruiseGap,
                                         accepted_gap_release=(short_gap_release or navigation_gap_release))
    if changed:
      self.long_cruise_gap = gap
      put_nonblocking("PrevCruiseGap", str(gap))

  def update_cruise_speed(self, controls, CS, longcontrol):
    car_set_speed = CS.cruiseState.speed * CV.MS_TO_KPH
    acc_enabled = bool(getattr(CS.cruiseState, 'enabledAcc', False)) and car_set_speed not in (0, 255)
    cruise_available = CS.cruiseState.available

    if acc_enabled:
      if longcontrol and self.speed_from_pcm == 1 and (not controls.enabled or not self.is_cruise_enabled):
        controls.v_cruise_kph = car_set_speed
      elif controls.v_cruise_kph <= 0 or controls.v_cruise_kph > MAX_SET_SPEED_KPH:
        controls.v_cruise_kph = car_set_speed

      if not self.is_cruise_enabled:
        self.is_cruise_enabled = True
        self.auto_cruise_control = False 
      if controls.enabled and self.long_active_user == 0 and self.auto_cruise_control and not self.user_cruise_paused:
        self._resume_longitudinal(controls, CS, 1)
    elif self.is_cruise_enabled:
      self.is_cruise_enabled = False
      if self.cruise_speed_min <= controls.v_cruise_kph <= MAX_SET_SPEED_KPH:
        self.v_cruise_kph_backup = controls.v_cruise_kph

    if not cruise_available:
      self.long_active_user = 0
      controls.v_cruise_kph = 0

    if longcontrol:
      controls.v_cruise_cluster_kph = controls.v_cruise_kph

  def apply_button_speed(self, speed_kph, button_type, long_press, v_ego):
    if long_press:
      delta = V_CRUISE_DELTA_KM if self.is_metric else V_CRUISE_DELTA_MI
      if button_type == ButtonType.accelCruise:
        speed_kph += delta - speed_kph % delta
      else:
        speed_kph -= delta - -speed_kph % delta
    elif button_type == ButtonType.accelCruise:
      # Resolve on every press so an unset CruiseSpeed1 follows the road limit
      # as it changes, instead of being frozen at param-read time.
      if self.cruise_button_mode == 3:
        self.cruise_speed_table = self.build_cruise_speed_table()
      if self.cruise_button_mode == 3 and self.cruise_speed_table:
        speed_kph = next((value for value in self.cruise_speed_table if value > speed_kph + 0.1),
                         ((speed_kph // self.cruise_speed_unit) + 1) * self.cruise_speed_unit)
      elif self.cruise_button_mode in (1, 2, 3):
        speed_kph = 30 if speed_kph < 30 else ((speed_kph // self.cruise_speed_unit) + 1) * self.cruise_speed_unit
      else:
        speed_kph += self.cruise_speed_unit_basic if self.is_metric else self.cruise_speed_unit_basic * CV.MPH_TO_KPH
    else:
      current_kph = v_ego * CV.MS_TO_KPH
      if self.cruise_button_mode in (2, 3) and current_kph > speed_kph + 2:
        speed_kph = current_kph
      else:
        unit = self.cruise_speed_unit if self.cruise_button_mode in (1, 2, 3) else self.cruise_speed_unit_basic
        speed_kph -= unit if self.is_metric else unit * CV.MPH_TO_KPH

    return float(clip(round(speed_kph, 1), self.cruise_speed_min, MAX_SET_SPEED_KPH))

  def update_controls(self, controls, CS, longcontrol):
    self.param_read_counter += 1
    if self.param_read_counter % 100 == 0:
      # The onroad driving-mode button should react within one second.
      self.read_driving_mode_params()
      self.read_cruise_params()

      # Refresh one larger group per second. Cruise button settings are read
      # above every second; the remaining settings apply within three seconds.
      readers = (self.read_curve_params, self.read_pedal_params,
                  self.read_navigation_params)
      readers[self.param_read_group]()
      self.param_read_group = (self.param_read_group + 1) % len(readers)

    self.update_driving_mode(CS, controls.sm)

    self.update_button_events(controls, CS, longcontrol)
    self._update_pedal_cruise(controls, CS)

  def inject_events(self, events):
    if self.slowing_down_sound_alert:
      self.slowing_down_sound_alert = False
      events.add(EventName.speedBumpSound if self.slowing_down_for_bump else EventName.slowingDownSpeedSound)
    elif self.slowing_down_alert:
      events.add(EventName.speedBump if self.slowing_down_for_bump else EventName.slowingDownSpeed)

  def cal_curve_speed(self, sm, v_ego, frame):
    # Retain the existing 5 Hz cadence on EON. Never smooth a stricter cap;
    # only debounce and ramp its release after a curve or a brief model dropout.
    if frame % 20 != 0:
      return
    dt = 20 * DT_CTRL if self.curve_update_frame is None else max(0, frame - self.curve_update_frame) * DT_CTRL
    self.curve_update_frame = frame
    valid = getattr(sm, 'valid', {}).get('modelV2', True) and getattr(sm, 'alive', {}).get('modelV2', True)
    self.curve_speed_ms = self.vision_curve_speed.update(
      sm['modelV2'] if valid else None, self.auto_curve_speed_factor,
      self.auto_curve_speed_lower_limit * CV.KPH_TO_MS,
      self.auto_curve_speed_decel_rate, dt)

  def update_max_speed(self, max_speed, longcontrol):
    # c3-wip applies the selected navigation target directly. The old 0.01
    # filter delayed both camera deceleration and speed recovery after passing.
    self.max_speed_clu = max_speed

  @staticmethod
  def calculate_navi_speed(left_dist, safe_speed_kph, safe_time, decel):
    """C3 carrot_serv.calculate_current_speed navigation deceleration."""
    safe_speed = safe_speed_kph * CV.KPH_TO_MS
    decel_dist = left_dist - safe_speed * safe_time
    if decel_dist <= 0.0:
      return safe_speed_kph
    return max(safe_speed_kph, min(250.0,
               np.sqrt(max(0.0, safe_speed ** 2 + 2.0 * decel * decel_dist)) * CV.MS_TO_KPH))

  def cal_max_speed(self, frame, CS, sm, clu11_speed, controls):
    limiter = get_road_speed_limiter()
    # Read only the raw roadLimitSpeed packet. The legacy limiter calculation
    # is intentionally bypassed so the C3 target is calculated exactly once.
    road_data = limiter.get_road_limit_speed()
    road_limit_speed = 0.0
    left_dist = 0.0
    apply_limit_speed = 0.0
    navi_source = ""
    navi_target_kph = 0.0
    cam_type = 0
    cam_dist = 0.0
    cam_limit = 0.0
    section_dist = 0.0
    section_limit = 0.0
    # Keep the lightweight shared-file bridge, but read it independently of
    # NOO/curve toggles so 7714 SDI and section data can feed the existing C3
    # navigation limiter. Existing roadLimitSpeed packets retain priority.
    navi_state = self.navigation_route.update()
    # TMAP prefers the legacy roadLimitSpeed camera/section fields when that
    # path has an active sender; NAVER always skips it and uses NAVER's 7714
    # SDI/section stream below instead. In this fork nothing currently sends
    # to the legacy UDP 2843 path for either app, so cam_dist/section_dist
    # stay 0 here and the navi_camera/navi_section fallback below (shared,
    # not naver_selected-gated) is what actually supplies both apps today.
    # Params are file reads; refresh the app selection once a second instead of
    # on every 100 Hz control frame (switching still applies within 1 s).
    if frame % 100 == 0 or self.nav_app_selected is None:
      self.nav_app_selected = self.params.get_int("EonClusterHudNavApp")
    naver_selected = self.nav_app_selected == 2
    if road_data is not None and not naver_selected:
      cam_type = int(road_data.camType)
      cam_dist = float(road_data.camLimitSpeedLeftDist)
      cam_limit = float(road_data.camLimitSpeed)
      section_dist = float(road_data.sectionLeftDist)
      section_limit = float(road_data.sectionLimitSpeed)

    navi_events = self.navigation_route.speed_events(navi_state)
    navi_camera = navi_events["camera"]
    navi_section = navi_events["section"]
    if cam_dist <= 0.0 and navi_camera is not None:
      cam_type = int(navi_camera["type"])
      cam_dist = float(navi_camera["distance"])
      cam_limit = float(navi_camera["limit"])
    if section_dist <= 0.0 and navi_section is not None:
      section_dist = float(navi_section["distance"])
      section_limit = float(navi_section["limit"])

    # c3-wip treats SDI type 22 as a speed bump and always replaces the
    # incoming camera limit with the configured bump target speed. This also
    # keeps bump control active when the navigation packet reports no limit.
    if cam_type == 22 and cam_dist > 0.0:
      cam_limit = self.auto_navi_speed_bump_speed

    # Match c3-wip's traveled-distance countdown using the actual elapsed
    # control time. Clamp long gaps so restarting controlsd cannot skip a
    # camera or section endpoint in one update.
    now = sec_since_boot()
    navi_dt = float(clip(now - self.navi_distance_time, 0.0, NAVI_DISTANCE_MAX_DT))
    self.navi_distance_time = now
    traveled = max(float(CS.out.vEgo), 0.0) * navi_dt

    if self.cam_dist_est > 0.0 and not self.cam_passed:
      self.cam_dist_est = max(0.0, self.cam_dist_est - traveled)
      # AutoNaviSpeedReleaseDist: 카메라 앞 N m 에서 미리 "지난 것"으로 보고 가속을 허용.
      if self.cam_dist_est <= self.auto_navi_speed_release_dist:
        self.cam_passed = True

    if cam_dist > 0.0 and cam_limit > 0.0:
      new_cam = self.cam_type_est < 0 or cam_type != self.cam_type_est or \
                abs(cam_limit - self.cam_limit_est) > 0.1 or cam_dist > self.cam_raw_dist + 100.0
      if new_cam:
        self.cam_dist_est = cam_dist
        self.cam_passed = False
      elif not self.cam_passed:
        self.cam_dist_est = cam_dist if self.cam_dist_est <= 0.0 else min(self.cam_dist_est, cam_dist)
      self.cam_raw_dist = cam_dist
      self.cam_limit_est = cam_limit
      self.cam_type_est = cam_type
      self.cam_last_valid_time = now
      cam_dist = 0.0 if self.cam_passed else self.cam_dist_est
    elif self.cam_limit_est > 0.0 and \
         now - self.cam_last_valid_time <= NAVI_DISTANCE_HOLD_TIME:
      # Preserve both an active countdown and a just-passed marker through a
      # brief zero packet. The latter prevents the same stale camera packet
      # from being acquired again immediately after passing it.
      cam_dist = 0.0 if self.cam_passed else self.cam_dist_est
      cam_limit = self.cam_limit_est
      cam_type = self.cam_type_est
    else:
      self.cam_dist_est = 0.0
      self.cam_raw_dist = 0.0
      self.cam_limit_est = 0.0
      self.cam_type_est = -1
      self.cam_last_valid_time = 0.0
      self.cam_passed = False
      cam_dist = 0.0
      cam_limit = 0.0

    if self.section_dist_est > 0.0 and not self.section_passed:
      self.section_dist_est = max(0.0, self.section_dist_est - traveled)
      if self.section_dist_est <= 0.0:
        self.section_passed = True

    if section_dist > 0.0 and section_limit > 0.0:
      new_section = self.section_limit_est <= 0.0 or \
                    abs(section_limit - self.section_limit_est) > 0.1 or \
                    section_dist > self.section_raw_dist + 100.0
      if new_section:
        self.section_dist_est = section_dist
        self.section_passed = False
      elif not self.section_passed:
        self.section_dist_est = section_dist if self.section_dist_est <= 0.0 else \
                                min(self.section_dist_est, section_dist)
      self.section_raw_dist = section_dist
      self.section_limit_est = section_limit
      self.section_last_valid_time = now
      section_dist = 0.0 if self.section_passed else self.section_dist_est
    elif self.section_limit_est > 0.0 and \
         now - self.section_last_valid_time <= NAVI_DISTANCE_HOLD_TIME:
      section_dist = 0.0 if self.section_passed else self.section_dist_est
      section_limit = self.section_limit_est
    else:
      self.section_dist_est = 0.0
      self.section_raw_dist = 0.0
      self.section_limit_est = 0.0
      self.section_last_valid_time = 0.0
      self.section_passed = False
      section_dist = 0.0
      section_limit = 0.0

    if cam_dist > 0.0 and cam_limit > 0.0:
      left_dist = cam_dist
      road_limit_speed = cam_limit
      if cam_type == 22:
        navi_source = "bump"
        navi_target_kph = self.auto_navi_speed_bump_speed
        safe_time = self.auto_navi_speed_bump_time
      else:
        navi_source = "cam"
        navi_target_kph = cam_limit * self.auto_navi_speed_safety_factor
        safe_time = self.auto_navi_speed_ctrl_end
      apply_kph = self.calculate_navi_speed(left_dist, navi_target_kph, safe_time,
                                            self.auto_navi_speed_decel_rate)
      apply_limit_speed = self.kph_to_clu(apply_kph)
    elif section_dist > 0.0 and section_limit > 0.0:
      left_dist = section_dist
      road_limit_speed = section_limit
      navi_source = "section"
      navi_target_kph = section_limit * self.auto_navi_speed_safety_factor
      # C3 holds the section target directly while inside the section.
      apply_limit_speed = self.kph_to_clu(navi_target_kph)
    if self.turn_vision_control:
      self.cal_curve_speed(sm, CS.out.vEgo, frame)
    else:
      self.vision_curve_speed.reset()
      self.curve_update_frame = None
      self.curve_speed_ms = UNLIMITED_SPEED
    cruise_speed_ms = controls.v_cruise_kph * CV.KPH_TO_MS
    self.apply_source = ""
    if self.turn_vision_control and self.curve_speed_ms < cruise_speed_ms:
      max_speed_clu = self.curve_speed_ms * self.speed_conv_to_clu
      self.apply_source = "vturn"
    else:
      max_speed_clu = self.kph_to_clu(controls.v_cruise_kph)

    if self.turn_vision_control:
      map_speed = self.navigation_route.cached_map_curve_speed_kph(
        navi_state, CS.out.vEgo * CV.MS_TO_KPH,
        self.map_turn_speed_factor, self.auto_curve_speed_lower_limit,
        self.auto_curve_speed_decel_rate)
      if map_speed is not None:
        map_speed_clu = self.kph_to_clu(map_speed)
        if map_speed_clu < max_speed_clu:
          max_speed_clu = map_speed_clu
          self.apply_source = "route"

    self.active_cam = road_limit_speed > 0 and left_dist > 0
    # carrot_navi_server.accepts() already gates the shared state file by
    # EonClusterHudNavApp, so navi_state["road_limit_kph"] is valid for
    # whichever app is selected -- it does not need a naver_selected check.
    # road_data.roadLimitSpeed is the legacy UDP 2843 path, which has no
    # sender anywhere in this fork; keep it only as a fallback in case that
    # ever changes, instead of excluding TMAP from the shared source.
    normal_road_limit_speed = float(navi_state.get("road_limit_kph", 0.0) or 0.0)
    if normal_road_limit_speed <= 0.0 and road_data is not None:
      normal_road_limit_speed = float(road_data.roadLimitSpeed)

    if apply_limit_speed >= self.kph_to_clu(10):
      if apply_limit_speed < max_speed_clu:
        max_speed_clu = apply_limit_speed
        self.apply_source = navi_source
      if clu11_speed > apply_limit_speed:
        self.slowing_down_for_bump = cam_type == 22 and cam_dist > 0.0
        if not self.slowing_down_alert and not self.slowing_down:
          self.slowing_down_sound_alert = True
          self.slowing_down = True
        self.slowing_down_alert = True
      else:
        self.slowing_down_alert = False
    else:
      self.slowing_down_alert = False
      self.slowing_down = False

    if self.noo_enabled and self.noo_mode in (0, 1, 3) and not CS.out.brakePressed:
      limits = self.navigation_route.speed_limits_kph(navi_state, self.noo_turn_speed,
                                                       self.noo_turn_end_time)
      limits = [value for value in limits if value is not None]
      if limits:
        noo_speed_clu = self.kph_to_clu(min(limits))
        if noo_speed_clu < max_speed_clu:
          max_speed_clu = noo_speed_clu
          self.apply_source = "noo"

    self.update_max_speed(int(max_speed_clu + 0.5), controls.CP.openpilotLongitudinalControl)
    return normal_road_limit_speed

  def sync_gas_speed(self, CS, clu11_speed, controls, longcontrol):
    if not (CS.gas_pressed and self.sync_set_speed_while_gas_pressed and CS.cruise_buttons == Buttons.NONE):
      return
    if clu11_speed + SYNC_MARGIN <= self.kph_to_clu(controls.v_cruise_kph):
      return
    set_speed = clip(clu11_speed + SYNC_MARGIN, self.min_set_speed_clu, self.max_set_speed_clu)
    set_speed_kph = float(set_speed * self.speed_conv_to_ms * CV.MS_TO_KPH)
    controls.v_cruise_kph = set_speed_kph
    if longcontrol:
      controls.v_cruise_cluster_kph = set_speed_kph
    self.target_speed = set_speed

  def update_target_speed(self, CS, clu11_speed, controls, longcontrol):
    if not longcontrol:
      self.sync_gas_speed(CS, clu11_speed, controls, False)
      self.target_speed = self.kph_to_clu(controls.v_cruise_kph)
      if self.max_speed_clu > self.min_set_speed_clu:
        self.target_speed = clip(self.target_speed, self.min_set_speed_clu, self.max_speed_clu)
    elif CS.cruiseState_enabled or self.long_active_user > 0:
      self.sync_gas_speed(CS, clu11_speed, controls, True)
      self.target_speed = self.kph_to_clu(controls.v_cruise_kph)

  def auto_speed_up(self, CS, controls, road_limit_speed, longcontrol):
    if road_limit_speed <= 0:
      self.last_road_limit_speed = road_limit_speed
      return
    if CS.cruise_buttons == Buttons.SET_DECEL:
      self.pause_auto_speed_up = True
    elif CS.cruise_buttons == Buttons.RES_ACCEL:
      self.pause_auto_speed_up = False

    set_speed_kph = controls.v_cruise_kph if longcontrol else CS.cruiseState_speed * CV.MS_TO_KPH
    if set_speed_kph <= 0:
      self.last_road_limit_speed = road_limit_speed
      return

    if self.last_road_limit_speed > 0 and road_limit_speed != self.last_road_limit_speed:
      if self.auto_road_speed_adjust < 0.0:
        set_speed_kph = road_limit_speed * self.auto_navi_speed_safety_factor if self.auto_road_speed_limit_offset < 0 \
                        else road_limit_speed + self.auto_road_speed_limit_offset
      elif road_limit_speed < self.last_road_limit_speed and self.auto_road_speed_adjust > 0.0:
        adjusted = road_limit_speed * self.auto_road_speed_adjust + set_speed_kph * (1.0 - self.auto_road_speed_adjust)
        set_speed_kph = min(set_speed_kph, adjusted)
      set_speed_kph = float(clip(set_speed_kph, self.cruise_speed_min, MAX_SET_SPEED_KPH))
      if longcontrol:
        controls.v_cruise_kph = set_speed_kph
        controls.v_cruise_cluster_kph = set_speed_kph
      else:
        self.target_speed = self.kph_to_clu(set_speed_kph)

    self.last_road_limit_speed = road_limit_speed
    road_limit_kph = float(clip(road_limit_speed * self.auto_speed_up_ratio,
                                0.0, MAX_SET_SPEED_KPH))
    if self.pause_auto_speed_up or road_limit_kph < 1.0:
      return
    lead = self.get_lead(controls.sm)
    if lead is None:
      return
    if lead.vLeadK * CV.MS_TO_KPH + 5 > set_speed_kph and set_speed_kph < road_limit_kph and lead.dRel < 60:
      new_speed = get_auto_speed_up_target(set_speed_kph, road_limit_kph)
      if longcontrol:
        controls.v_cruise_kph = new_speed
        controls.v_cruise_cluster_kph = new_speed
      else:
        self.target_speed = max(self.target_speed, self.kph_to_clu(new_speed))

  def update_scc(self, CC, CS, frame, controls, longcontrol):
    clu11_speed = CS.clu11["CF_Clu_Vanz"]
    road_limit_speed = self.cal_max_speed(frame, CS, controls.sm, clu11_speed, controls)

    cruise_set_speed = controls.v_cruise_kph if longcontrol else CS.cruiseState_speed * CV.MS_TO_KPH
    controls.applyMaxSpeed = float(clip(cruise_set_speed, self.cruise_speed_min,
                                       self.max_speed_clu * self.speed_conv_to_ms * CV.MS_TO_KPH))
    self.current_set_speed_kph = controls.applyMaxSpeed
    CC.sccSmoother.longControl = longcontrol
    CC.sccSmoother.applyMaxSpeed = controls.applyMaxSpeed
    # In C2 mode MAIN engages lateral control before SET/RES activates
    # longitudinal control. Publish no set speed until longitudinal is active
    # so the on-road HUD matches the cluster before SET/RES and after CANCEL.
    CC.sccSmoother.cruiseMaxSpeed = controls.v_cruise_kph if (not longcontrol or self.long_active_user > 0) else 0.0
    CC.sccSmoother.applySource = self.apply_source
    CC.sccSmoother.logMessage = ""

    self.update_target_speed(CS, clu11_speed, controls, longcontrol)
    self.auto_speed_up(CS, controls, road_limit_speed, longcontrol)

    stock_ascc_enabled = CS.acc_mode and CS.cruiseState_enabled and 1 < CS.cruiseState_speed < 255
    ascc_enabled = CC.enabled and not CS.brake_pressed and \
                   (stock_ascc_enabled or (longcontrol and self.long_active_user > 0))
    return clu11_speed, ascc_enabled

  def reset_scc_target(self):
    self.target_speed = 0.0

  def get_apply_accel(self, CS, sm, accel, stopping, dt=DT_CTRL):
    # Match the PID allowance at the final SCC12 boundary, including starts.
    # Braking/stopping requests bypass this positive-acceleration policy.
    cruise_max_accel = self.get_cruise_max_accel(CS.out.vEgo)
    if sm['radarState'].leadOne.status or sm['radarState'].leadTwo.status:
      cap = cruise_max_accel if self.follow_accel_limit is None else min(cruise_max_accel, self.follow_accel_limit)
      apply_accel = apply_cruise_max_limit(accel, stopping, cap)
    else:
      speed_error_kph = max(0.0, self.current_set_speed_kph - CS.out.vEgo * CV.MS_TO_KPH)
      apply_accel = apply_no_lead_cruise_accel_limit(
        accel, stopping, cruise_max_accel, speed_error_kph,
        self.no_lead_cruise_accel_factor, self.last_apply_accel,
        self.no_lead_cruise_jerk_limit, dt)
    self.last_apply_accel = float(apply_accel)
    return self.last_apply_accel

  def get_stock_cam_accel(self, apply_accel, stock_accel, scc11):
    stock_cam = scc11["Navi_SCC_Camera_Act"] == 2 and scc11["Navi_SCC_Camera_Status"] == 2
    self.stock_weight += DT_CTRL / 3.0 if stock_cam else -DT_CTRL / 3.0
    self.stock_weight = clip(self.stock_weight, 0.0, 1.0)
    accel = stock_accel * self.stock_weight + apply_accel * (1.0 - self.stock_weight)
    return min(accel, apply_accel), stock_cam

  def get_button(self, current_set_speed):
    if self.target_speed < self.min_set_speed_clu:
      return Buttons.NONE
    error = self.target_speed - current_set_speed
    if abs(error) < 0.9:
      return Buttons.NONE
    return Buttons.RES_ACCEL if error > 0 else Buttons.SET_DECEL
