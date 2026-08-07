#!/usr/bin/env python3
from collections import deque
from statistics import fmean, median


E2E_START_CONFIRM_TIME = 1.5
E2E_START_MIN_DISTANCE = 60.0
E2E_FAR_STOP_DISTANCE = 40.0
E2E_VISION_LEAD_DISTANCE = 90.0
E2E_VISION_LEAD_CONFIRM_TIME = 0.5


class ConditionalE2EController:
  """Select ACC/blended MPC and retain the model-predicted traffic-stop state."""

  def __init__(self, dt):
    self.dt = dt
    self.reset()

  def reset(self):
    self.stopping = False
    self.prepare = False
    self.stop_distance = 0.0
    self.stop_sign_count = 0
    self.start_sign_count = 0
    self.vision_lead_count = 0
    self.model_v_history = deque(maxlen=10)
    self.stop_x_median_history = deque(maxlen=3)
    self.stop_x_history = deque(maxlen=15)

  @property
  def traffic_state(self):
    return 2 if self.prepare else (1 if self.stopping else 0)

  @property
  def vision_lead_confirmed(self):
    return self.vision_lead_count * self.dt >= E2E_VISION_LEAD_CONFIRM_TIME

  def select_mode(self, experimental_mode, traffic_stop_mode):
    if experimental_mode:
      return 'blended'
    if traffic_stop_mode == 0:
      return 'acc'
    far_stop = self.stopping and self.stop_distance > E2E_FAR_STOP_DISTANCE
    apilot_vision_lead = traffic_stop_mode == 2 and self.vision_lead_confirmed
    return 'blended' if self.prepare or far_stop or apilot_vision_lead else 'acc'

  def update(self, *, available, experimental_mode, traffic_stop_mode, driving_mode, model_valid,
             model_x, model_y, model_v0, model_v_end, v_ego,
             steering_angle_deg, gas_pressed, brake_pressed, right_blinker,
             lead_present, radar_lead_present, radar_lead_distance,
             vision_lead_present):
    if not available:
      self.reset()
      return 'acc'

    traffic_stop_mode = max(0, min(2, traffic_stop_mode))
    if traffic_stop_mode == 0:
      self.reset()
      return 'blended' if experimental_mode else 'acc'

    # aPilot disables traffic-light stopping in HIGH/FAST mode. Explicit E2E
    # still remains blended, matching ExperimentalMode behavior.
    if driving_mode == 4:
      self.reset()
      return 'blended' if experimental_mode else 'acc'

    if not model_valid:
      self.reset()
      return 'blended' if experimental_mode else 'acc'

    self.model_v_history.append(float(model_v_end))
    model_v = fmean(self.model_v_history)
    self.stop_x_median_history.append(float(model_x))
    self.stop_x_history.append(float(median(self.stop_x_median_history)))
    filtered_stop_x = max(0.0, fmean(self.stop_x_history))
    v_ego_kph = v_ego * 3.6

    if v_ego_kph < 1.0:
      raw_stop_sign = model_x < 20.0 and model_v < 10.0
    elif v_ego_kph < 80.0:
      raw_stop_sign = (model_x < 120.0 and
                       (model_v < 3.0 or model_v < model_v0 * 0.7) and
                       abs(model_y) < 5.0)
    else:
      raw_stop_sign = False

    # Keep the aPilot start alternatives, with the existing distance guard and
    # sustained confirmation that prevent one noisy model frame from launching.
    raw_start_sign = (not raw_stop_sign and model_x > E2E_START_MIN_DISTANCE and
                      (model_v > 5.0 or model_v > model_v0 + 2.0))
    self.stop_sign_count = self.stop_sign_count + 1 if raw_stop_sign else 0
    self.start_sign_count = self.start_sign_count + 1 if raw_start_sign else 0
    stop_sign = self.stop_sign_count > 0 and not right_blinker
    start_sign = self.start_sign_count * self.dt >= E2E_START_CONFIRM_TIME

    self.vision_lead_count = self.vision_lead_count + 1 if vision_lead_present else 0
    radar_lead_before_stop = (radar_lead_present and radar_lead_distance > 0.0 and
                              radar_lead_distance - filtered_stop_x < 2.0)

    if self.stopping:
      if start_sign or gas_pressed:
        self.stopping = False
        self.prepare = True
        self.stop_distance = 0.0
      elif radar_lead_before_stop:
        # The real lead is closer than the model stop line; let ACC follow it.
        self.stopping = False
        self.prepare = False
        self.stop_distance = 0.0
      elif v_ego < 0.1:
        self.stop_distance = 0.0
      elif stop_sign:
        self.stop_distance = max(filtered_stop_x, v_ego ** 2 / 4.0)
      else:
        self.stop_distance = max(0.0, self.stop_distance - v_ego * self.dt)

    elif self.prepare:
      if brake_pressed or (v_ego_kph < 2.0 and not start_sign and
                           not lead_present and not gas_pressed):
        self.prepare = False
        self.stopping = True
        self.stop_distance = 0.0 if v_ego < 0.1 else filtered_stop_x
      elif v_ego_kph > 5.0 and model_x > E2E_START_MIN_DISTANCE:
        self.prepare = False

    elif (stop_sign and not lead_present and
          abs(steering_angle_deg) <= 5.0 and not gas_pressed):
      self.stopping = True
      self.stop_distance = 0.0 if v_ego < 0.1 else max(filtered_stop_x, v_ego ** 2 / 4.0)

    return self.select_mode(experimental_mode, traffic_stop_mode)
