import time
import numpy as np
from cereal import log
from common.realtime import DT_MDL
from common.conversions import Conversions as CV
from common.params import Params
from selfdrive.controls.lib.carrot_navi_atc import CarrotNaviAtc

AUTO_LCA_START_TIME = 1.0

LaneChangeState = log.LateralPlan.LaneChangeState
LaneChangeDirection = log.LateralPlan.LaneChangeDirection

LANE_CHANGE_SPEED_MIN = 50 * CV.KPH_TO_MS
LANE_CHANGE_TIME_MAX = 10.

DESIRES = {
  LaneChangeDirection.none: {
    LaneChangeState.off: log.LateralPlan.Desire.none,
    LaneChangeState.preLaneChange: log.LateralPlan.Desire.none,
    LaneChangeState.laneChangeStarting: log.LateralPlan.Desire.none,
    LaneChangeState.laneChangeFinishing: log.LateralPlan.Desire.none,
  },
  LaneChangeDirection.left: {
    LaneChangeState.off: log.LateralPlan.Desire.none,
    LaneChangeState.preLaneChange: log.LateralPlan.Desire.none,
    LaneChangeState.laneChangeStarting: log.LateralPlan.Desire.laneChangeLeft,
    LaneChangeState.laneChangeFinishing: log.LateralPlan.Desire.laneChangeLeft,
  },
  LaneChangeDirection.right: {
    LaneChangeState.off: log.LateralPlan.Desire.none,
    LaneChangeState.preLaneChange: log.LateralPlan.Desire.none,
    LaneChangeState.laneChangeStarting: log.LateralPlan.Desire.laneChangeRight,
    LaneChangeState.laneChangeFinishing: log.LateralPlan.Desire.laneChangeRight,
  },
}


class DesireHelper:
  def __init__(self):
    self.lane_change_state = LaneChangeState.off
    self.lane_change_direction = LaneChangeDirection.none
    self.lane_change_timer = 0.0
    self.lane_change_ll_prob = 1.0
    self.keep_pulse_timer = 0.0
    self.prev_one_blinker = False
    self.desire = log.LateralPlan.Desire.none

    self.params = Params()
    self.lane_change_enabled = self.params.get_bool('LaneChangeEnabled')
    self.auto_lane_change_enabled = self.params.get_bool('AutoLaneChangeEnabled')
    self.last_params_update = 0.0

    self.auto_lane_change_timer = 0.0
    self.prev_torque_applied = False

    # Lane Change Timer (AutoLaneChangeTimer) 관련
    self.lane_change_wait_timer = 0.0
    self.prev_lane_change = False
    self.road_edge = False
    self.carrot_atc = CarrotNaviAtc()
    self.carrot_atc_mode = 0

  def update(self, carstate, lateral_active, lane_change_prob, model_data=None):
    t = time.monotonic()
    if t - self.last_params_update > 1.0:
      self.lane_change_enabled = self.params.get_bool('LaneChangeEnabled')
      self.auto_lane_change_enabled = self.params.get_bool('AutoLaneChangeEnabled')
      try:
        self.carrot_atc_mode = int(self.params.get('CarrotAutoTurnControl', encoding='utf8') or '0')
      except (TypeError, ValueError):
        self.carrot_atc_mode = 0
      self.last_params_update = t

    # AutoLaneChangeTimer 파라미터 읽기 및 대기 시간 계산
    lane_change_set_timer = int(self.params.get("AutoLaneChangeTimer", encoding="utf8"))
    lane_change_auto_timer = 0.0 if lane_change_set_timer == 0 else \
                             0.1 if lane_change_set_timer == 1 else \
                             0.5 if lane_change_set_timer == 2 else \
                             1.0 if lane_change_set_timer == 3 else \
                             1.5 if lane_change_set_timer == 4 else 2.0

    v_ego = carstate.vEgo
    atc_state = self.carrot_atc.update()
    atc_steering = self.carrot_atc_mode in (1, 2) and lateral_active and not carstate.brakePressed
    atc_direction = atc_state['direction'] if atc_steering else 0
    opposite_torque = carstate.steeringPressed and ((atc_direction < 0 and carstate.steeringTorque < 0) or
                                                    (atc_direction > 0 and carstate.steeringTorque > 0))
    conflicting_blinker = (atc_direction < 0 and carstate.rightBlinker) or \
                          (atc_direction > 0 and carstate.leftBlinker)
    if opposite_torque or conflicting_blinker:
      atc_direction = 0
    atc_fork = atc_direction != 0 and atc_state['kind'] == 'fork' and \
               20.0 <= atc_state['distance'] <= min(350.0, max(160.0, v_ego * 12.0))
    left_blinker = carstate.leftBlinker or (atc_fork and atc_direction < 0)
    right_blinker = carstate.rightBlinker or (atc_fork and atc_direction > 0)
    one_blinker = left_blinker != right_blinker
    below_lane_change_speed = v_ego < LANE_CHANGE_SPEED_MIN

    # Road edge detection (FrogAi method)
    if one_blinker and model_data is not None:
      min_lane_threshold = 3.0
      blinker_index = 0 if left_blinker else 1
      desired_edge = model_data.roadEdges[blinker_index]
      current_lane = model_data.laneLines[blinker_index + 1]
      if all([desired_edge.x, desired_edge.y, current_lane.x, current_lane.y]) and \
         len(desired_edge.x) == len(current_lane.x):
        x = np.linspace(desired_edge.x[0], desired_edge.x[-1], num=len(desired_edge.x))
        lane_y = np.interp(x, current_lane.x, current_lane.y)
        desired_y = np.interp(x, desired_edge.x, desired_edge.y)
        lane_width = np.abs(desired_y - lane_y)
        self.road_edge = not (np.amax(lane_width) > min_lane_threshold)
      else:
        self.road_edge = True
    else:
      self.road_edge = False

    if (not lateral_active) or (self.lane_change_timer > LANE_CHANGE_TIME_MAX) or \
       (not one_blinker) or (not self.lane_change_enabled):
      self.lane_change_state = LaneChangeState.off
      self.lane_change_direction = LaneChangeDirection.none
      self.prev_lane_change = False
    else:
      torque_applied = carstate.steeringPressed and \
                       ((carstate.steeringTorque > 0 and self.lane_change_direction == LaneChangeDirection.left) or
                        (carstate.steeringTorque < 0 and self.lane_change_direction == LaneChangeDirection.right)) or \
                        self.auto_lane_change_enabled and \
                       (AUTO_LCA_START_TIME + 0.25) > self.auto_lane_change_timer > AUTO_LCA_START_TIME

      blindspot_detected = ((carstate.leftBlindspot and self.lane_change_direction == LaneChangeDirection.left) or
                            (carstate.rightBlindspot and self.lane_change_direction == LaneChangeDirection.right))

      if self.lane_change_state == LaneChangeState.off and one_blinker and \
         not self.prev_one_blinker and not below_lane_change_speed and not carstate.brakePressed:
        if left_blinker:
          self.lane_change_direction = LaneChangeDirection.left
        elif right_blinker:
          self.lane_change_direction = LaneChangeDirection.right

        self.lane_change_state = LaneChangeState.preLaneChange
        self.lane_change_ll_prob = 1.0
        self.lane_change_wait_timer = 0.0

      # preLaneChange: road edge 감지 시 차단
      elif self.lane_change_state == LaneChangeState.preLaneChange and self.road_edge:
        self.lane_change_direction = LaneChangeDirection.none

      # LaneChangeState.preLaneChange
      elif self.lane_change_state == LaneChangeState.preLaneChange:
        self.lane_change_wait_timer += DT_MDL

        if not one_blinker or below_lane_change_speed:
          self.lane_change_state = LaneChangeState.off
          self.prev_lane_change = False

        elif torque_applied and blindspot_detected and self.auto_lane_change_timer != 10.0:
          self.auto_lane_change_timer = 10.0
          self.prev_lane_change = False

        elif not torque_applied and self.auto_lane_change_timer == 10.0 and not self.prev_torque_applied:
          self.prev_torque_applied = True

        elif (torque_applied and (not blindspot_detected or self.prev_torque_applied)) or \
             (lane_change_auto_timer and
              self.lane_change_wait_timer > lane_change_auto_timer and
              not self.prev_lane_change and
              not blindspot_detected):
          self.lane_change_state = LaneChangeState.laneChangeStarting
          self.prev_lane_change = True

      # LaneChangeState.laneChangeStarting
      elif self.lane_change_state == LaneChangeState.laneChangeStarting:
        self.lane_change_ll_prob = max(self.lane_change_ll_prob - 2 * DT_MDL, 0.0)

        # 98% certainty
        if lane_change_prob < 0.02 and self.lane_change_ll_prob < 0.01:
          self.lane_change_state = LaneChangeState.laneChangeFinishing

      # LaneChangeState.laneChangeFinishing
      elif self.lane_change_state == LaneChangeState.laneChangeFinishing:
        # fade in laneline over 1s
        self.lane_change_ll_prob = min(self.lane_change_ll_prob + DT_MDL, 1.0)
        if self.lane_change_ll_prob > 0.99:
          self.lane_change_direction = LaneChangeDirection.none
          if one_blinker:
            self.lane_change_state = LaneChangeState.preLaneChange
          else:
            self.lane_change_state = LaneChangeState.off
            self.prev_lane_change = False

    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.preLaneChange):
      self.lane_change_timer = 0.0
    else:
      self.lane_change_timer += DT_MDL

    if self.lane_change_state == LaneChangeState.off:
      self.auto_lane_change_timer = 0.0
      self.prev_torque_applied = False
    elif self.auto_lane_change_timer < (AUTO_LCA_START_TIME + 0.25):
      self.auto_lane_change_timer += DT_MDL

    self.prev_one_blinker = one_blinker

    self.desire = DESIRES[self.lane_change_direction][self.lane_change_state]

    turn_direction = self.carrot_atc.steering_request(atc_state, v_ego) if atc_steering else 0
    if opposite_torque or conflicting_blinker:
      turn_direction = 0
    if turn_direction and self.lane_change_state == LaneChangeState.off:
      self.desire = log.LateralPlan.Desire.turnLeft if turn_direction < 0 else log.LateralPlan.Desire.turnRight

    # Send keep pulse once per second during LaneChangeStart.preLaneChange
    if self.lane_change_state in (LaneChangeState.off, LaneChangeState.laneChangeStarting):
      self.keep_pulse_timer = 0.0
    elif self.lane_change_state == LaneChangeState.preLaneChange:
      self.keep_pulse_timer += DT_MDL
      if self.keep_pulse_timer > 1.0:
        self.keep_pulse_timer = 0.0
      elif self.desire in (log.LateralPlan.Desire.keepLeft, log.LateralPlan.Desire.keepRight):
        self.desire = log.LateralPlan.Desire.none
