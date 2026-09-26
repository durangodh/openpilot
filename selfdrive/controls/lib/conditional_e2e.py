#!/usr/bin/env python3
from collections import deque
from statistics import fmean, median


# Match C3's confirmed green/start response. The old-model branch still uses
# the filtered model trajectory and distance guard below to reject a single
# noisy far-path frame.
E2E_START_CONFIRM_TIME = 0.2
E2E_START_MIN_DISTANCE = 60.0
E2E_FAR_STOP_DISTANCE = 40.0
E2E_VISION_LEAD_DISTANCE = 90.0
E2E_VISION_LEAD_CONFIRM_TIME = 0.5
E2E_LEAD_DROPOUT_CONFIRM_TIME = 0.5
E2E_MODE_RELEASE_HOLD_TIME = 0.0
TRAFFIC_STOP_SOLVER_COMFORT_BRAKE = 2.5
TRAFFIC_STOP_APILOT_COMFORT_BRAKE = 2.5

# Published through LongitudinalPlan.e2eReason for the compact onroad badge.
# Keep these values stable because the Qt UI consumes them directly.
E2E_REASON_OFF = 0
E2E_REASON_ACC = 1
E2E_REASON_SIGNAL = 2
E2E_REASON_VISION_LEAD = 3
E2E_REASON_DEPARTURE = 4
E2E_REASON_MANUAL = 5


def adjust_stop_distance_for_decel(stop_distance, v_ego, decel_factor, distance_adjust=0.0):
  """Emulate a variable MPC comfort-brake value with a fixed-parameter solver.

  aPilot changes the comfort-brake MPC parameter while stopping for a traffic
  signal. This branch uses a pre-generated solver with that value compiled in,
  so shifting the virtual stop obstacle by the equivalent braking-distance
  delta provides the same earlier/later braking request without regenerating
  the acados solver.
  """
  factor = max(0.1, min(1.2, float(decel_factor)))
  speed = max(0.0, float(v_ego))
  base_distance = speed ** 2 / (2.0 * TRAFFIC_STOP_SOLVER_COMFORT_BRAKE)
  adjusted_distance = speed ** 2 / (2.0 * TRAFFIC_STOP_APILOT_COMFORT_BRAKE * factor)
  return max(0.0, float(stop_distance) + float(distance_adjust) -
             (adjusted_distance - base_distance))


def update_latched_stop_distance(stop_distance, observed_distance, v_ego, dt):
  """Dead-reckon a confirmed stop point without allowing it to move away."""
  remaining_distance = max(0.0, float(stop_distance) - max(0.0, float(v_ego)) * float(dt))
  return min(remaining_distance, max(0.0, float(observed_distance)))


class ConditionalE2EController:
  """Select ACC/blended MPC and retain the model-predicted traffic-stop state."""

  def __init__(self, dt):
    self.dt = dt
    self.vision_lead_confirm_frames = max(1, round(E2E_VISION_LEAD_CONFIRM_TIME / dt))
    self.mode_release_hold_frames = max(1, round(E2E_MODE_RELEASE_HOLD_TIME / dt))
    self.reset()

  def reset(self):
    self.stopping = False
    self.prepare = False
    self.stop_distance = 0.0
    self.stop_sign_count = 0
    self.start_sign_count = 0
    self.vision_lead_count = 0
    self.vision_lead_latched = False
    self.lead_dropout_confirm_frames = max(1, round(E2E_LEAD_DROPOUT_CONFIRM_TIME / self.dt))
    self.lead_missing_count = 0
    self.lead_recent = False
    self.mode_release_hold_count = 0
    self.model_v_history = deque(maxlen=10)
    self.stop_x_median_history = deque(maxlen=3)
    self.stop_x_history = deque(maxlen=15)
    self.reason = E2E_REASON_OFF

  @property
  def traffic_state(self):
    return 2 if self.prepare else (1 if self.stopping else 0)

  @property
  def vision_lead_confirmed(self):
    return self.vision_lead_latched

  def select_mode(self, experimental_mode, traffic_stop_mode):
    if experimental_mode:
      self.reason = E2E_REASON_MANUAL
      return 'blended'
    if traffic_stop_mode == 0:
      self.reason = E2E_REASON_ACC
      return 'acc'
    far_stop = self.stopping and self.stop_distance > E2E_FAR_STOP_DISTANCE
    apilot_vision_lead = traffic_stop_mode == 2 and self.vision_lead_confirmed
    hold_blended = self.mode_release_hold_count > 0
    if self.prepare:
      self.reason = E2E_REASON_DEPARTURE
      return 'blended'
    if far_stop:
      self.reason = E2E_REASON_SIGNAL
      return 'blended'
    if apilot_vision_lead:
      self.reason = E2E_REASON_VISION_LEAD
      return 'blended'
    if hold_blended:
      # The current release hold is a single planner tick. Preserve the most
      # recent E2E reason rather than flashing ACC during that transition.
      if self.reason not in (E2E_REASON_SIGNAL, E2E_REASON_VISION_LEAD, E2E_REASON_DEPARTURE):
        self.reason = E2E_REASON_MANUAL
      return 'blended'
    self.reason = E2E_REASON_ACC
    return 'acc'

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
      return self.select_mode(experimental_mode, traffic_stop_mode)

    # aPilot disables traffic-light stopping in HIGH/FAST mode. Explicit E2E
    # still remains blended, matching ExperimentalMode behavior.
    if driving_mode == 4:
      self.reset()
      self.reason = E2E_REASON_MANUAL if experimental_mode else E2E_REASON_ACC
      return 'blended' if experimental_mode else 'acc'

    if not model_valid:
      self.reset()
      self.reason = E2E_REASON_MANUAL if experimental_mode else E2E_REASON_ACC
      return 'blended' if experimental_mode else 'acc'

    if self.mode_release_hold_count > 0:
      self.mode_release_hold_count -= 1

    self.model_v_history.append(float(model_v_end))
    model_v = fmean(self.model_v_history)
    self.stop_x_median_history.append(float(model_x))
    self.stop_x_history.append(float(median(self.stop_x_median_history)))
    filtered_stop_x = max(0.0, fmean(self.stop_x_history))
    v_ego_kph = v_ego * 3.6

    if v_ego_kph < 1.0:
      raw_stop_sign = model_x < 20.0 and model_v < 10.0
    elif v_ego_kph < 82.0:
      # Match c3-wip's check_model_stopping(): the model's own path endpoint
      # can round out into a phantom "stop" well behind a real lead car, so
      # require it to also sit closer than the actual lead. No lead present
      # -> no cap (radar_lead_distance is 0.0 either way in that case, so use
      # a large fallback rather than letting it block every stop check).
      lead_distance_guard = radar_lead_distance if radar_lead_present else 1000.0
      # Higher speed needs more runway to legitimately see a real stop that
      # far out; scale the distance cap from 120 m at <=60 km/h up to 150 m
      # at >=80 km/h instead of a single fixed 120 m for the whole bracket.
      if v_ego_kph <= 60.0:
        distance_cap = 120.0
      elif v_ego_kph >= 80.0:
        distance_cap = 150.0
      else:
        distance_cap = 120.0 + (v_ego_kph - 60.0) * 1.5
      raw_stop_sign = (model_x < lead_distance_guard - 3.0 and
                       model_x < distance_cap and
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

    # Confirm both acquisition and release. Radar/vision classification can
    # flicker for a frame near standstill; dropping E2E immediately creates a
    # sharp ACC/E2E acceleration discontinuity exactly when brake hold is
    # handing off to launch control.
    if vision_lead_present:
      self.vision_lead_count = min(self.vision_lead_confirm_frames,
                                   self.vision_lead_count + 1)
      if self.vision_lead_count >= self.vision_lead_confirm_frames:
        self.vision_lead_latched = True
    else:
      self.vision_lead_count = max(0, self.vision_lead_count - 1)
      if self.vision_lead_count == 0:
        self.vision_lead_latched = False

    # Do not enter traffic-stop control on a brief lead dropout. A lead that
    # was already being tracked must be absent continuously before the model
    # stop can take ownership; a scene that never had a lead is unaffected.
    if lead_present:
      self.lead_recent = True
      self.lead_missing_count = 0
    elif self.lead_recent:
      self.lead_missing_count += 1
      if self.lead_missing_count >= self.lead_dropout_confirm_frames:
        self.lead_recent = False
        self.lead_missing_count = 0
    # A live lead must always keep ACC ownership until the dropout timer
    # confirms it is really gone.
    effective_lead_present = lead_present or self.lead_recent

    # A confirmed vision lead is just as valid as a radar lead for deciding
    # that the real stopped vehicle lies before the model's traffic stop.
    confirmed_lead_before_stop = ((radar_lead_present or self.vision_lead_latched) and
                                  radar_lead_distance > 0.0 and
                                  radar_lead_distance - filtered_stop_x < 2.0)

    if self.stopping:
      if start_sign or gas_pressed:
        self.stopping = False
        self.prepare = True
        self.mode_release_hold_count = 0
        self.stop_distance = 0.0
      elif confirmed_lead_before_stop:
        # The real lead is closer than the model stop line; let ACC follow it.
        self.stopping = False
        self.prepare = False
        self.stop_distance = 0.0
      elif v_ego < 0.1:
        self.stop_distance = 0.0
      elif stop_sign:
        # The model can shift its endpoint from the stop line toward overhead
        # signal heads in a large intersection. Keep the road-fixed stop point
        # acquired on entry, while still accepting a newly observed closer stop.
        observed_stop_distance = max(filtered_stop_x, v_ego ** 2 / 4.0)
        self.stop_distance = update_latched_stop_distance(
          self.stop_distance, observed_stop_distance, v_ego, self.dt)
      else:
        self.stop_distance = max(0.0, self.stop_distance - v_ego * self.dt)

    elif self.prepare:
      prepare_abort = (v_ego_kph < 2.0 and not start_sign and
                       not lead_present and not gas_pressed)
      if brake_pressed or prepare_abort:
        self.prepare = False
        self.stopping = True
        self.mode_release_hold_count = self.mode_release_hold_frames
        self.stop_distance = 0.0 if v_ego < 0.1 else filtered_stop_x
      elif v_ego_kph > 5.0 and model_x > E2E_START_MIN_DISTANCE:
        self.prepare = False
        self.mode_release_hold_count = self.mode_release_hold_frames

    elif (stop_sign and not effective_lead_present and
          abs(steering_angle_deg) <= 5.0 and not gas_pressed):
      self.stopping = True
      self.stop_distance = 0.0 if v_ego < 0.1 else max(filtered_stop_x, v_ego ** 2 / 4.0)

    return self.select_mode(experimental_mode, traffic_stop_mode)
