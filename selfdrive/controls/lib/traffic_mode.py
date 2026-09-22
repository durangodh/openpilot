#!/usr/bin/env python3
"""Traffic Mode v2 adapted from FrogPilot 891a81448fce63e2942bdcc282fcff55bfc6e0c6.

Keep the upstream profile; use the caller's acceleration ceiling and distance
model so SAFE/ECO, cornering limits and configured stop distance stay effective.
"""
import math
import numpy as np

MAX_T_FOLLOW = 3.0

TRAFFIC_ACCEL_BP  = [ 0.0,  2.0,  5.0, 10.0, 15.0, 25.0]
TRAFFIC_MIN_ACCEL = [-1.4, -1.5, -1.7, -2.0, -2.4, -2.8]
TRAFFIC_MAX_ACCEL = [1.05, 1.35, 1.45, 1.15, 0.85, 0.65]

TRAFFIC_FOLLOW_BP = [ 0.0,   2.0,   5.0,  10.0,  15.0,  20.0,  25.0]
TRAFFIC_FOLLOW    = [ 0.5, 0.583, 0.638, 0.939, 1.197, 1.150, 1.150]

TRAFFIC_JERK_BP             = [ 0.0,  5.0, 15.0, 25.0]
TRAFFIC_ACCEL_JERK          = [ 1.0,  1.0,  0.9,  0.8]
TRAFFIC_DANGER_JERK         = [ 1.2,  1.2, 1.25,  1.3]
TRAFFIC_DECEL_JERK          = [ 1.2,  1.2,  1.1,  1.0]
TRAFFIC_SPEED_DECREASE_JERK = [ 1.2,  1.2,  1.1,  1.0]
TRAFFIC_SPEED_JERK          = [ 1.0,  1.0,  0.9,  0.8]

TRAFFIC_DANGER_FACTOR_MIN = 0.70
TRAFFIC_DANGER_FACTOR_MAX = 0.90

TRAFFIC_SEVERITY_DISTANCE_FLOOR = 1.0

TRAFFIC_JERK_MAX = 2.0

TRAFFIC_LAUNCH_JERK = 0.75
TRAFFIC_LAUNCH_SPEED = 2.0

TRAFFIC_MIN_T_FOLLOW = 0.5

TRAFFIC_PULLAWAY_SPEED = 8.0

TRAFFIC_T_FOLLOW_DECREASE_RATE = 0.2
TRAFFIC_T_FOLLOW_INCREASE_RATE = 0.4
TRAFFIC_T_FOLLOW_SAFETY_RATE = 1.2


class TrafficMode:
  def __init__(self, dt):
    self.dt = dt
    self.base_danger_factor = 0.8
    self.reset()

  def reset(self):
    self.active = False

    self.acceleration_jerk = 0
    self.danger_jerk = 0
    self.max_accel = 0
    self.min_accel = 0
    self.speed_jerk = 0
    self.t_follow = 0

    self.filtered_t_follow = None

    self.danger_factor = self.base_danger_factor

  def update(self, sm, *, available, model_distance, model_time, max_accel,
             min_accel, stop_distance, comfort_brake, danger_factor, initial_t_follow):
    lead = sm["radarState"].leadOne
    values = (sm["carState"].vEgo, sm["carState"].aEgo, lead.dRel,
              lead.vLead, lead.vRel, lead.aLeadK, model_distance, model_time,
              max_accel, min_accel, stop_distance, comfort_brake, danger_factor, initial_t_follow)
    if (not available or not lead.status or not all(math.isfinite(v) for v in values) or
        sm["carState"].vEgo < 0 or lead.dRel <= 0 or model_distance < 0 or
        model_time <= 0 or comfort_brake <= 0 or min_accel >= 0):
      self.reset()
      return

    # Include a closing lead projected to enter the model horizon, as upstream.
    tracking_lead = (lead.dRel < model_distance + stop_distance or
                     (lead.vRel < 0 and
                      lead.dRel + lead.vRel * model_time < model_distance + stop_distance))
    # The model path can collapse when stopped; retain an already tracked lead.
    tracking_lead |= self.active and sm["carState"].standstill
    if not tracking_lead:
      self.reset()
      return

    self.base_danger_factor = danger_factor
    self.brake_limit = min_accel
    self.normal_max_accel = max_accel
    self.stop_distance = stop_distance
    self.comfort_brake = comfort_brake
    if not self.active:
      # A live toggle/new lead must not immediately shrink the previous gap.
      self.filtered_t_follow = float(np.clip(initial_t_follow, TRAFFIC_MIN_T_FOLLOW, MAX_T_FOLLOW))
    self.active = True

    base_t_follow = self.get_base_t_follow(sm)
    desired_distance = self.desired_follow_distance(sm["carState"].vEgo, max(sm["radarState"].leadOne.vLead, 0), base_t_follow)
    gap_error = self.calculate_gap_error(desired_distance, sm)
    braking_severity = self.get_braking_severity(desired_distance, gap_error, base_t_follow, sm)
    pullaway_severity = self.get_pullaway_severity(desired_distance, gap_error, base_t_follow, sm)

    target_t_follow = base_t_follow + ((MAX_T_FOLLOW - base_t_follow) * braking_severity)
    if sm["carState"].vEgo < TRAFFIC_PULLAWAY_SPEED and pullaway_severity > 0:
      target_t_follow -= (base_t_follow - TRAFFIC_MIN_T_FOLLOW) * pullaway_severity
    target_t_follow = float(np.clip(target_t_follow, TRAFFIC_MIN_T_FOLLOW, MAX_T_FOLLOW))
    self.t_follow = self.update_t_follow(target_t_follow, braking_severity > 0)

    desired_distance = self.desired_follow_distance(sm["carState"].vEgo, max(sm["radarState"].leadOne.vLead, 0), self.t_follow)
    gap_error = self.calculate_gap_error(desired_distance, sm)
    braking_severity = self.get_braking_severity(desired_distance, gap_error, self.t_follow, sm)
    pullaway_severity = self.get_pullaway_severity(desired_distance, gap_error, self.t_follow, sm)

    self.update_acceleration_limits(braking_severity, pullaway_severity, sm)
    self.update_danger_factor(braking_severity, pullaway_severity)
    self.update_jerk(braking_severity, pullaway_severity, sm)

  def desired_follow_distance(self, v_ego, v_lead, t_follow):
    return max(0.0, (v_ego**2 - v_lead**2) / (2 * self.comfort_brake) +
               t_follow * v_ego + self.stop_distance)

  def calculate_gap_error(self, desired_distance, sm):
    return sm["radarState"].leadOne.dRel - desired_distance

  def get_base_t_follow(self, sm):
    return float(np.clip(np.interp(sm["carState"].vEgo, TRAFFIC_FOLLOW_BP, TRAFFIC_FOLLOW), TRAFFIC_MIN_T_FOLLOW, MAX_T_FOLLOW))

  def get_braking_severity(self, desired_distance, gap_error, t_follow, sm):
    return float(np.clip((
      (max(-sm["radarState"].leadOne.vRel, 0) * t_follow) +
      ((np.clip(-sm["radarState"].leadOne.aLeadK, 0, -self.brake_limit) * t_follow**2) / 2) -
      gap_error
    ) / max(desired_distance, TRAFFIC_SEVERITY_DISTANCE_FLOOR), 0, 1))

  def get_pullaway_severity(self, desired_distance, gap_error, t_follow, sm):
    if sm["radarState"].leadOne.vRel <= 0:
      return 0

    return float(np.clip(((sm["radarState"].leadOne.vRel * t_follow) + gap_error) / max(desired_distance, TRAFFIC_SEVERITY_DISTANCE_FLOOR), 0, 1))

  def update_acceleration_limits(self, braking_severity, pullaway_severity, sm):
    self.max_accel = float(np.interp(sm["carState"].vEgo, TRAFFIC_ACCEL_BP, TRAFFIC_MAX_ACCEL))
    if pullaway_severity > 0:
      self.max_accel += (max(self.normal_max_accel, self.max_accel) - self.max_accel) * pullaway_severity

    self.max_accel = min(self.max_accel, self.normal_max_accel)

    self.min_accel = float(np.interp(sm["carState"].vEgo, TRAFFIC_ACCEL_BP, TRAFFIC_MIN_ACCEL))
    if braking_severity > 0:
      self.min_accel += (self.brake_limit - self.min_accel) * braking_severity

  def update_danger_factor(self, braking_severity, pullaway_severity):
    self.danger_factor = self.base_danger_factor
    if braking_severity > 0:
      self.danger_factor += (TRAFFIC_DANGER_FACTOR_MAX - self.base_danger_factor) * braking_severity
    if pullaway_severity > 0:
      self.danger_factor -= (self.base_danger_factor - TRAFFIC_DANGER_FACTOR_MIN) * pullaway_severity
    self.danger_factor = float(np.clip(self.danger_factor, TRAFFIC_DANGER_FACTOR_MIN, TRAFFIC_DANGER_FACTOR_MAX))

  def update_jerk(self, braking_severity, pullaway_severity, sm):
    if sm["carState"].aEgo >= 0:
      self.acceleration_jerk = np.interp(sm["carState"].vEgo, TRAFFIC_JERK_BP, TRAFFIC_ACCEL_JERK)
      self.speed_jerk = np.interp(sm["carState"].vEgo, TRAFFIC_JERK_BP, TRAFFIC_SPEED_JERK)
    else:
      self.acceleration_jerk = np.interp(sm["carState"].vEgo, TRAFFIC_JERK_BP, TRAFFIC_DECEL_JERK)
      self.speed_jerk = np.interp(sm["carState"].vEgo, TRAFFIC_JERK_BP, TRAFFIC_SPEED_DECREASE_JERK)

    self.danger_jerk = np.interp(sm["carState"].vEgo, TRAFFIC_JERK_BP, TRAFFIC_DANGER_JERK)

    if sm["carState"].vEgo < TRAFFIC_LAUNCH_SPEED and pullaway_severity > 0:
      self.acceleration_jerk += (min(self.acceleration_jerk, TRAFFIC_LAUNCH_JERK) - self.acceleration_jerk) * pullaway_severity
      self.speed_jerk += (min(self.speed_jerk, TRAFFIC_LAUNCH_JERK) - self.speed_jerk) * pullaway_severity

    if braking_severity > 0:
      self.danger_jerk += (TRAFFIC_JERK_MAX - self.danger_jerk) * braking_severity
      if sm["carState"].aEgo < 0:
        self.acceleration_jerk += (TRAFFIC_JERK_MAX - self.acceleration_jerk) * braking_severity
        self.speed_jerk += (TRAFFIC_JERK_MAX - self.speed_jerk) * braking_severity

  def update_t_follow(self, target_t_follow, safety_increase):
    if self.filtered_t_follow is None:
      self.filtered_t_follow = target_t_follow
      return target_t_follow

    if target_t_follow > self.filtered_t_follow:
      if safety_increase:
        rate = TRAFFIC_T_FOLLOW_SAFETY_RATE
      else:
        rate = TRAFFIC_T_FOLLOW_INCREASE_RATE
    else:
      rate = TRAFFIC_T_FOLLOW_DECREASE_RATE

    max_delta = rate * self.dt
    self.filtered_t_follow += float(np.clip(target_t_follow - self.filtered_t_follow, -max_delta, max_delta))
    return self.filtered_t_follow
