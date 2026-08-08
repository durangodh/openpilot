import numpy as np

from cereal import car
from common.conversions import Conversions as CV
from common.numpy_fast import clip, interp
from common.params import Params, put_nonblocking
from common.realtime import DT_CTRL
from selfdrive.car.hyundai.values import Buttons
from selfdrive.controls.lib.carrot_navi_atc import CarrotNaviAtc
from selfdrive.controls.lib.drive_helpers import V_CRUISE_MAX, V_CRUISE_MIN, V_CRUISE_DELTA_KM, V_CRUISE_DELTA_MI
from selfdrive.controls.lib.low_speed_long import AutoResumeController, STOCK_SCC_LEADLESS_MIN_SPEED_KPH, \
  read_cruise_speed_min
from selfdrive.controls.lib.lateral_planner import TRAJECTORY_SIZE
from selfdrive.road_speed_limiter import get_road_speed_limiter


SYNC_MARGIN = 3.0
MIN_SET_SPEED_KPH = V_CRUISE_MIN
MAX_SET_SPEED_KPH = V_CRUISE_MAX
MIN_CURVE_SPEED = 32.0 * CV.KPH_TO_MS

ButtonType = car.CarState.ButtonEvent.Type
EventName = car.CarEvent.EventName


class CruiseHelper:
  """Owns cruise policy; Hyundai CAN transmission stays in SccSmoother."""

  def __init__(self, params=None):
    self.params = params or Params()
    self.param_read_counter = 0

    self.button_count = 0
    self.button_long_pressed = False
    self.button_prev = ButtonType.unknown

    self.is_cruise_enabled = False
    # aPilot keeps this as runtime state: CANCEL blocks automatic re-engagement
    # until the driver explicitly enables cruise again.
    self.auto_cruise_control = True
    self.long_cruise_gap = 4
    self.init_driving_mode = 3
    self.my_driving_mode = 3
    self.last_mode_param = 3
    self.driving_mode_index = 0.0
    self.safe_mode_base_factor = 0.8
    self.my_safe_mode_factor = 1.0

    self.target_speed = 0.0
    self.max_speed_clu = 0.0
    self.curve_speed_ms = 255.0
    self.active_cam = False
    self.over_speed_limit = False
    self.slowing_down = False
    self.slowing_down_alert = False
    self.slowing_down_sound_alert = False
    self.limited_lead = False
    self.stock_weight = 0.0

    self.auto_resume = AutoResumeController()
    self.auto_resume_request = False
    self.auto_resume_set_speed_kph = 0.0
    self.carrot_atc = CarrotNaviAtc()
    self.last_road_limit_speed = 0.0
    self.pause_auto_speed_up = False

    self.read_params()

  def read_params(self):
    self.is_metric = self.params.get_bool("IsMetric")
    self.speed_conv_to_ms = CV.KPH_TO_MS if self.is_metric else CV.MPH_TO_MS
    self.speed_conv_to_clu = CV.MS_TO_KPH if self.is_metric else CV.MS_TO_MPH

    self.cruise_speed_min = read_cruise_speed_min(self.params)
    self.min_set_speed_clu = self.kph_to_clu(self.cruise_speed_min)
    self.max_set_speed_clu = self.kph_to_clu(MAX_SET_SPEED_KPH)
    self.speed_from_pcm = int(clip(self.params.get_int("SpeedFromPCM"), 1, 2))
    self.cruise_button_mode = int(clip(self.params.get_int("CruiseButtonMode"), 0, 3))
    self.cruise_speed_unit = int(clip(self.params.get_int("CruiseSpeedUnit"), 1, 20))
    self.cruise_speed_unit_basic = int(clip(self.params.get_int("CruiseSpeedUnitBasic"), 1, 10))
    self.cruise_button_long_delay = int(clip(self.params.get_int("CruiseButtonLongDelay"), 30, 150))
    table = [self.params.get_int(f"CruiseSpeed{i}") for i in range(1, 6)]
    self.cruise_speed_table = sorted(float(clip(v, self.cruise_speed_min, MAX_SET_SPEED_KPH)) for v in table)

    self.slow_on_curves = self.params.get_bool("SccSmootherSlowOnCurves")
    self.sync_set_speed_while_gas_pressed = self.params.get_bool("SccSmootherSyncGasPressed")
    self.auto_gas_resume_guard = self.params.get_bool("AutoGasResumeGuard")
    self.auto_resume_from_gas = int(clip(self.params.get_int("AutoResumeFromGas"), 0, 2))
    self.auto_resume_from_gas_speed = float(clip(self.params.get_int("AutoGasTokSpeed"), 5, 60))
    self.auto_gas_cancel_speed = float(clip(self.params.get_int("AutoGasCancelSpeed"), 0, 60))
    self.auto_resume_from_gas_speed_mode = int(clip(self.params.get_int("AutoResumeFromGasSpeedMode"), 0, 2))
    self.auto_resume_from_brake_release = self.params.get_bool("AutoResumeFromBrakeRelease")
    self.auto_resume_from_brake_speed = float(clip(self.params.get_int("AutoResumeFromBrakeCarSpeed"), 5, 60))
    self.auto_resume_from_brake_distance = float(clip(self.params.get_int("AutoResumeFromBrakeReleaseDist"), 2, 50))

    self.auto_speed_up_ratio = float(self.params.get_int("AutoSpeedUptoRoadSpeedLimit")) * 0.01
    self.auto_road_speed_adjust = float(clip(self.params.get_int("AutoRoadSpeedAdjust"), -100, 100)) * 0.01
    self.auto_road_speed_limit_offset = float(clip(self.params.get_int("AutoRoadSpeedLimitOffset"), -30, 30))
    self.auto_navi_speed_safety_factor = float(clip(self.params.get_int("AutoNaviSpeedSafetyFactor"), 80, 120)) * 0.01
    self.carrot_atc_mode = int(clip(self.params.get_int("CarrotAutoTurnControl"), 0, 3))
    self.carrot_atc_speed = float(clip(self.params.get_int("CarrotAutoTurnSpeed"), 5, 80))
    self.carrot_atc_end_time = float(clip(self.params.get_int("CarrotAutoTurnEndTime"), 1, 20))

    self.long_cruise_gap = int(clip(self.params.get_int("PrevCruiseGap"), 1, 4))

    self.init_driving_mode = int(clip(self.params.get_int("InitMyDrivingMode"), 1, 5))
    if self.param_read_counter == 0:
      self.my_driving_mode = 3 if self.init_driving_mode == 5 else self.init_driving_mode
      self.last_mode_param = self.params.get_int("MyDrivingMode")
    self.safe_mode_base_factor = float(clip(self.params.get_int("MySafeModeFactor") * 0.01, 0.5, 1.0))
    self.update_safe_mode_factor()

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

  def update_driving_mode(self, CS, sm):
    lead = self.get_lead(sm)
    accel_index = interp(CS.aEgo, [-3.0, -1.0, 0.0, 1.0, 3.0], [100.0, 0.0, 0.0, 0.0, 100.0])
    velocity_index = interp(CS.vEgo * CV.MS_TO_KPH, [0.0, 5.0, 50.0], [100.0, 80.0, 0.0])
    total_index = accel_index * 3.0 + velocity_index if lead is not None and 0.0 < lead.dRel < 50.0 else 0.0
    self.driving_mode_index = self.driving_mode_index * 0.999 + total_index * 0.001

    if self.init_driving_mode == 5 and self.driving_mode_index > 0.0 and self.my_driving_mode not in (2, 4):
      if self.driving_mode_index < 20.0:
        self.my_driving_mode = 3
      elif self.driving_mode_index > 80.0:
        self.my_driving_mode = 1
    self.update_safe_mode_factor()

  def update_button_events(self, controls, CS, longcontrol):
    self.update_cruise_speed(controls, CS, longcontrol)
    self.sync_physical_gap(CS)

    # Match aPilot's user-cancel latch. Process this before the cruise-enabled
    # guard since SCC may already be disabled by the time the release arrives.
    if any(event.type == ButtonType.cancel and not event.pressed for event in CS.buttonEvents):
      self.auto_cruise_control = False
      self.button_count = 0
      self.button_long_pressed = False

    if not self.is_cruise_enabled or not controls.enabled:
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
        if event.type in (ButtonType.accelCruise, ButtonType.decelCruise) and not self.button_long_pressed:
          controls.v_cruise_kph = self.apply_button_speed(controls.v_cruise_kph, event.type, False, CS.vEgo)
        self.button_count = 0
        self.button_long_pressed = False

    if self.button_count > self.cruise_button_long_delay:
      self.button_long_pressed = True
      if self.button_prev in (ButtonType.accelCruise, ButtonType.decelCruise):
        controls.v_cruise_kph = self.apply_button_speed(controls.v_cruise_kph, self.button_prev, True, CS.vEgo)
        self.button_count %= self.cruise_button_long_delay

    if longcontrol:
      controls.v_cruise_cluster_kph = controls.v_cruise_kph

  def sync_physical_gap(self, CS):
    gap = int(CS.cruiseGap)
    if 1 <= gap <= 4 and gap != self.long_cruise_gap:
      self.long_cruise_gap = gap
      put_nonblocking("PrevCruiseGap", str(gap))

  def update_cruise_speed(self, controls, CS, longcontrol):
    car_set_speed = CS.cruiseState.speed * CV.MS_TO_KPH
    # Button handling follows the actual SCC ACC state. openpilot longitudinal
    # control deliberately has pcmCruise=False, so gating on pcmCruise made
    # physical RES/SET releases disappear even while ACC was active.
    cruise_enabled = car_set_speed not in (0, 255) and CS.cruiseState.enabled

    if cruise_enabled:
      base_speed = car_set_speed if longcontrol and self.speed_from_pcm == 1 and not controls.enabled else controls.v_cruise_kph
      controls.v_cruise_kph = base_speed
    else:
      controls.v_cruise_kph = 0

    if self.is_cruise_enabled != cruise_enabled:
      self.is_cruise_enabled = cruise_enabled
      if cruise_enabled:
        self.auto_cruise_control = True
      controls.v_cruise_kph = car_set_speed if cruise_enabled else 0
      controls.LoC.reset(v_pid=CS.vEgo)

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
    if self.param_read_counter % 100 == 0:
      self.read_params()
    self.param_read_counter += 1

    self.update_driving_mode(CS, controls.sm)
    mode = self.params.get_int("MyDrivingMode")
    if mode != self.last_mode_param and 1 <= mode <= 4:
      self.my_driving_mode = mode
      self.last_mode_param = mode
      self.driving_mode_index = -100.0
      self.update_safe_mode_factor()

    self.update_button_events(controls, CS, longcontrol)

  def inject_events(self, events):
    if self.slowing_down_sound_alert:
      self.slowing_down_sound_alert = False
      events.add(EventName.slowingDownSpeedSound)
    elif self.slowing_down_alert:
      events.add(EventName.slowingDownSpeed)

  @staticmethod
  def get_initial_gap_button(ascc_enabled, CS):
    # The physical GAP button and the stock SCC gap are the only gap input.
    # Never synthesize GAP presses to chase a stored software value.
    return Buttons.NONE

  def cal_curve_speed(self, sm, v_ego, frame):
    if frame % 20 != 0:
      return
    model = sm['modelV2']
    if len(model.position.x) != TRAJECTORY_SIZE or len(model.position.y) != TRAJECTORY_SIZE:
      self.curve_speed_ms = 255.0
      return
    x = model.position.x
    y = model.position.y
    dy = np.gradient(y, x)
    d2y = np.gradient(dy, x)
    curvature = d2y / (1 + dy ** 2) ** 1.5
    start = int(interp(v_ego, [10.0, 27.0], [10, TRAJECTORY_SIZE - 10]))
    curve_segment = curvature[start:min(start + 10, TRAJECTORY_SIZE)]
    a_y_max = 2.975 - v_ego * 0.0375
    curve_speed = np.sqrt(a_y_max / np.clip(np.abs(curve_segment), 1e-4, None))
    model_speed = np.mean(curve_speed) * 0.85
    self.curve_speed_ms = float(max(model_speed, MIN_CURVE_SPEED)) if model_speed < v_ego else 255.0
    if np.isnan(self.curve_speed_ms):
      self.curve_speed_ms = 255.0

  def update_max_speed(self, max_speed, limited_curve, longcontrol):
    if not longcontrol or self.max_speed_clu <= 0:
      self.max_speed_clu = max_speed
    else:
      self.max_speed_clu += (max_speed - self.max_speed_clu) * 0.01

  def cal_max_speed(self, frame, CS, sm, clu11_speed, controls):
    limiter = get_road_speed_limiter()
    apply_limit_speed, road_limit_speed, left_dist, first_started, _ = limiter.get_max_speed(clu11_speed, self.is_metric)
    self.cal_curve_speed(sm, CS.out.vEgo, frame)

    curve_limited = False
    if self.slow_on_curves and self.curve_speed_ms >= MIN_CURVE_SPEED:
      max_speed_clu = min(controls.v_cruise_kph * CV.KPH_TO_MS, self.curve_speed_ms) * self.speed_conv_to_clu
      curve_limited = True
    else:
      max_speed_clu = self.kph_to_clu(controls.v_cruise_kph)

    self.active_cam = road_limit_speed > 0 and left_dist > 0
    normal_road_limit_speed = 0.0
    if limiter.roadLimitSpeed is not None:
      normal_road_limit_speed = float(limiter.roadLimitSpeed.roadLimitSpeed)
      camera_factor = clip(limiter.roadLimitSpeed.camSpeedFactor, 1.0, 1.1)
      self.over_speed_limit = limiter.roadLimitSpeed.camLimitSpeedLeftDist > 0 and \
                              0 < road_limit_speed * camera_factor < clu11_speed + 2
    else:
      self.over_speed_limit = False

    if apply_limit_speed >= self.kph_to_clu(10):
      if first_started:
        self.max_speed_clu = clu11_speed
      max_speed_clu = min(max_speed_clu, apply_limit_speed)
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
      limits = self.carrot_atc.speed_limits_kph(self.carrot_atc.update(), self.carrot_atc_speed,
                                                self.carrot_atc_end_time)
      limits = [value for value in limits if value is not None]
      if limits:
        max_speed_clu = min(max_speed_clu, self.kph_to_clu(min(limits)))

    self.update_max_speed(int(max_speed_clu + 0.5), curve_limited, controls.CP.openpilotLongitudinalControl)
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
    elif CS.cruiseState_enabled:
      self.sync_gas_speed(CS, clu11_speed, controls, True)

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
    if self.param_read_counter % 100 == 0:
      self.read_params()
    clu11_speed = CS.clu11["CF_Clu_Vanz"]
    road_limit_speed = self.cal_max_speed(frame, CS, controls.sm, clu11_speed, controls)

    cruise_set_speed = controls.v_cruise_kph if longcontrol else CS.cruiseState_speed * CV.MS_TO_KPH
    controls.applyMaxSpeed = float(clip(cruise_set_speed, self.cruise_speed_min,
                                       self.max_speed_clu * self.speed_conv_to_ms * CV.MS_TO_KPH))
    CC.sccSmoother.longControl = longcontrol
    CC.sccSmoother.applyMaxSpeed = controls.applyMaxSpeed
    CC.sccSmoother.cruiseMaxSpeed = controls.v_cruise_kph
    CC.sccSmoother.logMessage = ""

    lead = self.get_lead(controls.sm)
    lead_distance = lead.dRel if lead is not None else 0.0
    traffic_state = controls.sm['longitudinalPlan'].trafficState
    self.auto_resume_request, self.auto_resume_set_speed_kph = self.auto_resume.update(
      available=CC.enabled and self.auto_cruise_control,
      cruise_enabled=CS.cruiseState_enabled,
      gas_mode=self.auto_resume_from_gas,
      gas_resume_speed_kph=self.auto_resume_from_gas_speed,
      gas_cancel_speed_kph=self.auto_gas_cancel_speed,
      speed_mode=self.auto_resume_from_gas_speed_mode,
      brake_release_enabled=self.auto_resume_from_brake_release,
      brake_resume_speed_kph=self.auto_resume_from_brake_speed,
      brake_lead_distance=self.auto_resume_from_brake_distance,
      cruise_speed_min=self.cruise_speed_min,
      gas_pressed=CS.out.gasPressed,
      gas=CS.out.gas,
      brake_pressed=CS.out.brakePressed,
      v_ego=CS.out.vEgo,
      steering_angle_deg=CS.out.steeringAngleDeg,
      left_blinker=CS.out.leftBlinker,
      right_blinker=CS.out.rightBlinker,
      traffic_state=traffic_state,
      has_lead=lead is not None,
      lead_distance=lead_distance,
      previous_speed_kph=CS.prev_cruiseState_speed * CV.MS_TO_KPH,
      safety_guard=self.auto_gas_resume_guard,
      dt=DT_CTRL)

    self.update_target_speed(CS, clu11_speed, controls, longcontrol)
    self.auto_speed_up(CS, controls, road_limit_speed, longcontrol)

    ascc_enabled = CS.acc_mode and CC.enabled and CS.cruiseState_enabled and \
                   1 < CS.cruiseState_speed < 255 and not CS.brake_pressed
    stock_resume_available = clu11_speed >= STOCK_SCC_LEADLESS_MIN_SPEED_KPH or \
                             (not longcontrol and CS.obj_valid and lead_distance > 1)
    ascc_auto_set = self.auto_resume_request and stock_resume_available
    initial_gap_button = self.get_initial_gap_button(ascc_enabled, CS)
    return clu11_speed, ascc_enabled, ascc_auto_set, initial_gap_button

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
