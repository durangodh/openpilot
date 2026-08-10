import numpy as np

from cereal import car, log
from common.conversions import Conversions as CV
from common.numpy_fast import clip, interp
from common.params import Params, put_nonblocking
from common.realtime import DT_CTRL
from selfdrive.car.hyundai.values import Buttons
from selfdrive.controls.lib.carrot_navi_atc import CarrotNaviAtc
from selfdrive.controls.lib.drive_helpers import V_CRUISE_MAX, V_CRUISE_MIN, V_CRUISE_DELTA_KM, V_CRUISE_DELTA_MI
from selfdrive.controls.lib.gap_sync import select_physical_gap, select_software_gap
from selfdrive.road_speed_limiter import get_road_speed_limiter


SYNC_MARGIN = 3.0
MIN_SET_SPEED_KPH = V_CRUISE_MIN
MAX_SET_SPEED_KPH = V_CRUISE_MAX
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
    self.auto_cruise_control = True
    self.long_active_user = 0
    self.long_active_user_ready = 0
    self.user_cruise_paused = False
    self.v_cruise_kph_backup = float(MIN_SET_SPEED_KPH)
    self.prev_brake_pressed = False
    self.gas_pressed_count = 0
    self.pre_gas_pressed_max = 0.0
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
    self.init_driving_mode = 3
    self.my_driving_mode = 3
    self.last_mode_param = 3
    self.driving_mode_index = 0.0
    self.safe_mode_base_factor = 0.8
    self.my_safe_mode_factor = 1.0

    self.target_speed = 0.0
    self.max_speed_clu = 0.0
    self.curve_speed_ms = 250.0 * CV.KPH_TO_MS
    self.map_curve_speed_kph = 250.0
    self.apply_source = ""
    self.active_cam = False
    self.over_speed_limit = False
    self.slowing_down = False
    self.slowing_down_alert = False
    self.slowing_down_sound_alert = False
    self.limited_lead = False
    self.stock_weight = 0.0

    self.carrot_atc = CarrotNaviAtc()
    self.empty_navi_state = self.carrot_atc.empty_state()
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
    table = [self.params.get_int(f"CruiseSpeed{i}") for i in range(1, 6)]
    self.cruise_speed_table = sorted(float(clip(v, self.cruise_speed_min, MAX_SET_SPEED_KPH)) for v in table)
    self.sync_set_speed_while_gas_pressed = self.params.get_bool("SccSmootherSyncGasPressed")

  def read_curve_params(self):
    self.turn_vision_control = self.params.get_bool("TurnVisionControl")
    curve_factor = self.params.get_int("AutoCurveSpeedFactor")
    curve_lower = self.params.get_int("AutoCurveSpeedLowerLimit")
    map_factor = self.params.get_int("MapTurnSpeedFactor")
    navi_decel = self.params.get_int("AutoNaviSpeedDecelRate")
    self.auto_curve_speed_factor = float(clip(curve_factor if curve_factor > 0 else 120, 50, 300)) * 0.01
    self.auto_curve_speed_lower_limit = float(clip(curve_lower if curve_lower > 0 else 30, 5, 80))
    self.map_turn_speed_factor = float(clip(map_factor if map_factor > 0 else 90, 50, 150)) * 0.01
    self.auto_navi_speed_decel_rate = float(clip(navi_decel if navi_decel > 0 else 120, 10, 300)) * 0.01

  def read_pedal_params(self):
    # C2 pedal-resume settings. Existing branch keys are used so no unregistered
    # Params access can crash controlsd.
    self.auto_resume_from_gas_speed = float(clip(self.params.get_int("AutoGasTokSpeed"), 5, 160))
    self.auto_gas_cancel_speed = float(clip(self.params.get_int("AutoGasCancelSpeed"), 0, 160))
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
    self.auto_navi_speed_ctrl_end = float(clip(ctrl_end if ctrl_end > 0 else 7, 3, 20))
    self.auto_navi_speed_bump_time = float(clip(bump_time if bump_time > 0 else 1, 1, 50))
    self.auto_navi_speed_bump_speed = float(clip(bump_speed if bump_speed > 0 else 35, 10, 100))
    self.auto_navi_speed_safety_factor = float(clip(safety_factor if safety_factor > 0 else 105, 80, 120)) * 0.01
    self.carrot_atc_mode = int(clip(self.params.get_int("CarrotAutoTurnControl"), 0, 3))
    self.carrot_atc_speed = float(clip(self.params.get_int("CarrotAutoTurnSpeed"), 5, 80))
    self.carrot_atc_end_time = float(clip(self.params.get_int("CarrotAutoTurnEndTime"), 1, 20))

  def read_driving_mode_params(self, initialize=False):
    self.init_driving_mode = int(clip(self.params.get_int("InitMyDrivingMode"), 1, 5))
    mode = self.params.get_int("MyDrivingMode")
    if initialize:
      self.my_driving_mode = 3 if self.init_driving_mode == 5 else self.init_driving_mode
      self.last_mode_param = mode
    elif mode != self.last_mode_param and 1 <= mode <= 4:
      self.my_driving_mode = mode
      self.last_mode_param = mode
      self.driving_mode_index = -100.0
    self.safe_mode_base_factor = float(clip(self.params.get_int("MySafeModeFactor") * 0.01, 0.5, 1.0))
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

  def kph_to_clu(self, kph):
    return int(kph * CV.KPH_TO_MS * self.speed_conv_to_clu)

  @staticmethod
  def get_lead(sm):
    lead = sm['radarState'].leadOne
    return lead if lead.status else None

  def update_safe_mode_factor(self):
    if self.my_driving_mode == 2:
      self.my_safe_mode_factor = self.safe_mode_base_factor
    elif self.my_driving_mode == 1:
      self.my_safe_mode_factor = (1.0 + self.safe_mode_base_factor) / 2.0
    else:
      self.my_safe_mode_factor = 1.0

  def _resume_longitudinal(self, controls, CS, active_mode=1):
    if self.long_active_user <= 0:
      controls.LoC.reset(v_pid=CS.vEgo)
    self.long_active_user = active_mode
    self.long_active_user_ready = active_mode
    self.user_cruise_paused = False
    self.auto_cruise_control = True

  def _pause_longitudinal(self, controls, user_cancel=False):
    if self.long_active_user > 0 and self.cruise_speed_min <= controls.v_cruise_kph <= MAX_SET_SPEED_KPH:
      self.v_cruise_kph_backup = controls.v_cruise_kph
    self.long_active_user = 0 if user_cancel else -2
    self.long_active_user_ready = 0
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
    if v_ego_kph < 20.0:
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
      controls.v_cruise_kph = float(clip(v_ego_kph, self.cruise_speed_min, MAX_SET_SPEED_KPH))
      self._resume_longitudinal(controls, CS, 3)
    elif self.d_rel <= 0.0 and v_ego_kph >= self.auto_resume_from_brake_car_speed > 0.0:
      controls.v_cruise_kph = float(clip(v_ego_kph, self.cruise_speed_min, MAX_SET_SPEED_KPH))
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
      self.long_active_user_ready = 0
    elif brake_pressed:
      if not self.prev_brake_pressed:
        self._pause_longitudinal(controls)
    elif self.prev_brake_pressed:
      self._brake_release_resume(controls, CS)

    if controls.enabled and CS.gasPressed:
      self.gas_pressed_count += 1
      self.gas_pressed_frame = self.param_read_counter
      self.pre_gas_pressed_max = max(self.pre_gas_pressed_max, float(CS.gas))

      if self.long_active_user <= 0 and self.auto_resume_from_gas > 0 and self.auto_cruise_control and \
         self.traffic_state != 1 and self._resume_guard_ok(CS) and \
         (v_ego_kph >= self.auto_resume_from_gas_speed or CS.gas >= 0.6):
        self._select_resume_speed(controls, CS)
        self._resume_longitudinal(controls, CS, 3)
      elif self.long_active_user > 0 and 0.0 < self.auto_gas_cancel_speed and v_ego_kph < self.auto_gas_cancel_speed:
        self._pause_longitudinal(controls)

      if self.auto_resume_from_gas_speed < v_ego_kph and v_ego_kph > controls.v_cruise_kph:
        controls.v_cruise_kph = float(clip(v_ego_kph, self.cruise_speed_min, MAX_SET_SPEED_KPH))
    elif self.gas_pressed_count > 0:
      quick_release = self.gas_pressed_count * DT_CTRL < 0.6 and self.pre_gas_pressed_max > 0.03
      if quick_release and self.auto_resume_from_gas > 1 and self.long_active_user <= 0 and \
         self.auto_cruise_control and v_ego_kph >= self.auto_resume_from_gas_speed and self._resume_guard_ok(CS):
        self._select_resume_speed(controls, CS)
        self._resume_longitudinal(controls, CS, 3)
      self.gas_pressed_count = 0
      self.pre_gas_pressed_max = 0.0

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

    auto_mode = self.my_driving_mode
    if self.init_driving_mode == 5 and self.driving_mode_index > 0.0 and self.my_driving_mode not in (2, 4):
      if self.driving_mode_index < 20.0:
        auto_mode = 3
      elif self.driving_mode_index > 80.0:
        auto_mode = 1
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

    # apilot-c2 CANCEL latch: pause longitudinal only and require an explicit
    # RES/SET before automatic SCC resume is allowed again. Lateral control is
    # untouched - controlsd stays engaged until cruise MAIN turns off.
    if any(event.type == ButtonType.cancel and not event.pressed for event in CS.buttonEvents):
      self._pause_longitudinal(controls, user_cancel=True)
      self.button_count = 0
      self.button_long_pressed = False
      return

    # RES/SET controls longitudinal activation while lateral control remains
    # engaged, including after brake/cancel/standstill.
    if not controls.enabled:
      self.button_count = 0
      self.button_long_pressed = False
      return

    if self.button_count > 0:
      self.button_count += 1

    for event in CS.buttonEvents:
      if event.pressed and self.button_count == 0 and event.type in (ButtonType.accelCruise,
                                                                     ButtonType.decelCruise):
        self.button_count = 1
        self.button_prev = event.type
      elif not event.pressed and self.button_count > 0:
        if event.type in (ButtonType.accelCruise, ButtonType.decelCruise):
          if self.long_active_user <= 0:
            current_kph = float(clip(CS.vEgoCluster * CV.MS_TO_KPH,
                                     self.cruise_speed_min, MAX_SET_SPEED_KPH))
            if event.type == ButtonType.accelCruise:
              controls.v_cruise_kph = max(current_kph, self.v_cruise_kph_backup,
                                          controls.v_cruise_kph if controls.v_cruise_kph <= MAX_SET_SPEED_KPH else 0.0)
            else:
              controls.v_cruise_kph = current_kph
            self._resume_longitudinal(controls, CS, 1)
          elif not self.button_long_pressed:
            controls.v_cruise_kph = self.apply_button_speed(controls.v_cruise_kph, event.type, False, CS.vEgo)
        self.button_count = 0
        self.button_long_pressed = False

    if self.button_count > self.cruise_button_long_delay:
      self.button_long_pressed = True
      if self.button_prev in (ButtonType.accelCruise, ButtonType.decelCruise):
        controls.v_cruise_kph = self.apply_button_speed(controls.v_cruise_kph, self.button_prev, True, CS.vEgo)
        self._resume_longitudinal(controls, CS, 1)
        self.button_count %= self.cruise_button_long_delay

    if longcontrol:
      controls.v_cruise_cluster_kph = controls.v_cruise_kph

  def sync_physical_gap(self, controls, CS, longcontrol):
    # Hyundai SCC falls back to gap 4 as cruise disengages. Do not treat that
    # passive fallback as a driver choice or persist it over PrevCruiseGap.
    # Still accept an explicit physical gap-button event while disengaged.
    gap_button_events = [event for event in CS.buttonEvents if event.type == ButtonType.gapAdjustCruise]
    if longcontrol:
      # The stock SCC can restart at gap 4 even when PrevCruiseGap is 2. Cycle
      # from the persisted value on a real button press and ignore passive SCC
      # fallback values, keeping planner and cluster behavior deterministic.
      gap_button_pressed = any(event.pressed for event in gap_button_events)
      gap, changed = select_software_gap(self.long_cruise_gap, gap_button_pressed)
    else:
      gap, changed = select_physical_gap(self.long_cruise_gap, CS.cruiseGap,
                                         controls.enabled, bool(gap_button_events))
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
        self.auto_cruise_control = True
      if controls.enabled and self.long_active_user == 0 and self.auto_cruise_control and not self.user_cruise_paused:
        self._resume_longitudinal(controls, CS, 1)
    elif self.is_cruise_enabled:
      self.is_cruise_enabled = False
      if self.cruise_speed_min <= controls.v_cruise_kph <= MAX_SET_SPEED_KPH:
        self.v_cruise_kph_backup = controls.v_cruise_kph

    if not cruise_available:
      self.long_active_user = 0
      self.long_active_user_ready = 0
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
      if self.cruise_button_mode == 3 and self.cruise_speed_table:
        speed_kph = next((value for value in self.cruise_speed_table if value > speed_kph + 0.1),
                         speed_kph + self.cruise_speed_unit)
      elif self.cruise_button_mode in (1, 2):
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

      # Refresh one larger group per second. All non-mode settings still apply
      # live within four seconds, without a large synchronous I/O burst.
      readers = (self.read_cruise_params, self.read_curve_params,
                 self.read_pedal_params, self.read_navigation_params)
      readers[self.param_read_group]()
      self.param_read_group = (self.param_read_group + 1) % len(readers)

    self.update_driving_mode(CS, controls.sm)

    self.update_button_events(controls, CS, longcontrol)
    self._update_pedal_cruise(controls, CS)

  def inject_events(self, events):
    if self.slowing_down_sound_alert:
      self.slowing_down_sound_alert = False
      events.add(EventName.slowingDownSpeedSound)
    elif self.slowing_down_alert:
      events.add(EventName.slowingDownSpeed)

  def cal_curve_speed(self, sm, v_ego, frame):
    """carrot-wip vision curve speed using predicted lateral acceleration."""
    # modelV2 changes much slower than controlsd's 100 Hz loop. Keep the C2
    # cadence to avoid repeating the same NumPy work on every control frame.
    if frame % 20 != 0:
      return

    orientation_rates = np.asarray(sm['modelV2'].orientationRate.z, dtype=np.float64)
    velocities = np.asarray(sm['modelV2'].velocity.x, dtype=np.float64)
    if len(orientation_rates) == 0 or len(orientation_rates) != len(velocities):
      self.curve_speed_ms = 250.0 * CV.KPH_TO_MS
      return

    orientation_rates *= self.auto_curve_speed_factor
    valid = np.isfinite(orientation_rates) & np.isfinite(velocities)
    if not np.any(valid):
      self.curve_speed_ms = 250.0 * CV.KPH_TO_MS
      return

    max_pred_lat_acc = float(np.max(np.abs(orientation_rates[valid]) * velocities[valid]))
    v_ego = max(float(v_ego), 0.1)
    max_curve = max_pred_lat_acc / (v_ego ** 2)
    if max_curve <= 1e-6:
      turn_speed_kph = 250.0
    else:
      turn_speed_kph = float(clip(
        np.sqrt(1.9 / max_curve) * CV.MS_TO_KPH,
        self.auto_curve_speed_lower_limit, 250.0))
    self.curve_speed_ms = turn_speed_kph * CV.KPH_TO_MS

  def update_max_speed(self, max_speed, longcontrol):
    if not longcontrol or self.max_speed_clu <= 0:
      self.max_speed_clu = max_speed
    else:
      self.max_speed_clu += (max_speed - self.max_speed_clu) * 0.01

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
    if road_data is not None:
      cam_type = int(road_data.camType)
      cam_dist = float(road_data.camLimitSpeedLeftDist)
      cam_limit = float(road_data.camLimitSpeed)
      section_dist = float(road_data.sectionLeftDist)
      section_limit = float(road_data.sectionLimitSpeed)

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
      self.curve_speed_ms = 250.0 * CV.KPH_TO_MS
    navi_enabled = self.turn_vision_control or self.carrot_atc_mode in (2, 3)
    navi_state = self.carrot_atc.update() if navi_enabled else self.empty_navi_state

    cruise_speed_ms = controls.v_cruise_kph * CV.KPH_TO_MS
    self.apply_source = ""
    if self.turn_vision_control and self.curve_speed_ms < cruise_speed_ms:
      max_speed_clu = self.curve_speed_ms * self.speed_conv_to_clu
      self.apply_source = "vturn"
    else:
      max_speed_clu = self.kph_to_clu(controls.v_cruise_kph)

    self.map_curve_speed_kph = 250.0
    if self.turn_vision_control:
      map_speed = self.carrot_atc.cached_map_curve_speed_kph(
        navi_state, CS.out.vEgo * CV.MS_TO_KPH,
        self.map_turn_speed_factor, self.auto_curve_speed_lower_limit,
        self.auto_navi_speed_decel_rate)
      if map_speed is not None:
        self.map_curve_speed_kph = map_speed
        map_speed_clu = self.kph_to_clu(map_speed)
        if map_speed_clu < max_speed_clu:
          max_speed_clu = map_speed_clu
          self.apply_source = "route"

    self.active_cam = road_limit_speed > 0 and left_dist > 0
    normal_road_limit_speed = 0.0
    if road_data is not None:
      normal_road_limit_speed = float(road_data.roadLimitSpeed)
      self.over_speed_limit = road_data.camLimitSpeedLeftDist > 0 and \
                              0 < navi_target_kph < clu11_speed + 2
    else:
      self.over_speed_limit = False

    if apply_limit_speed >= self.kph_to_clu(10):
      if apply_limit_speed < max_speed_clu:
        max_speed_clu = apply_limit_speed
        self.apply_source = navi_source
      if clu11_speed > apply_limit_speed:
        if not self.slowing_down_alert and not self.slowing_down:
          self.slowing_down_sound_alert = True
          self.slowing_down = True
        self.slowing_down_alert = True
      else:
        self.slowing_down_alert = False
    else:
      self.slowing_down_alert = False
      self.slowing_down = False

    if self.carrot_atc_mode in (2, 3) and not CS.out.brakePressed:
      limits = self.carrot_atc.speed_limits_kph(navi_state, self.carrot_atc_speed,
                                                self.carrot_atc_end_time)
      limits = [value for value in limits if value is not None]
      if limits:
        atc_speed_clu = self.kph_to_clu(min(limits))
        if atc_speed_clu < max_speed_clu:
          max_speed_clu = atc_speed_clu
          self.apply_source = "atc"

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
    road_limit_kph = road_limit_speed * self.auto_speed_up_ratio
    if self.pause_auto_speed_up or road_limit_kph < 1.0:
      return
    lead = self.get_lead(controls.sm)
    if lead is None:
      return
    if lead.vLeadK * CV.MS_TO_KPH + 5 > set_speed_kph and set_speed_kph < road_limit_kph and lead.dRel < 60:
      new_speed = min(set_speed_kph + 5, road_limit_kph)
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
    CC.sccSmoother.longControl = longcontrol
    CC.sccSmoother.applyMaxSpeed = controls.applyMaxSpeed
    CC.sccSmoother.cruiseMaxSpeed = controls.v_cruise_kph
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

  @staticmethod
  def get_apply_accel(CS, sm, accel, stopping):
    return accel

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
