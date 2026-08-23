import math


class NavigationLaneChangeController:
  """Fail-closed TMAP lane planner for Navigation on Openpilot.

  It never treats TMAP's current_lane as the ego lane.  The ego lane comes
  from modelV2 geometry, while TMAP available[] supplies only the set of route
  compatible lanes.  One adjacent lane is requested at a time.
  """

  MIN_DISTANCE = 80.0
  MAX_DISTANCE = 1200.0
  CONFIRM_FRAMES = 10       # 0.5 s at model rate
  LANE_UPDATE_FRAMES = 40   # fail closed after 2 s without camera confirmation

  def __init__(self):
    self.reset()

  def reset(self):
    self.event_key = None
    self.canceled = False
    self.completed = False
    self.open_count = {-1: 0, 1: 0}
    self.requested_direction = 0
    self.finishing_seen = False
    self.waiting_from_lane = 0
    self.waiting_frames = 0
    self.current_lane = 0
    self.target_lane = 0
    self.lane_count = 0

  @staticmethod
  def _ints(value, count):
    if not isinstance(value, list) or len(value) < count:
      return None
    try:
      return [int(value[index]) for index in range(count)]
    except (TypeError, ValueError):
      return None

  @classmethod
  def lane_plan(cls, state, ego_lane):
    if not isinstance(state, dict) or not state.get("route_fresh", False) or state.get("off_route", False):
      return None
    if not isinstance(ego_lane, dict):
      return None
    try:
      current = int(ego_lane.get("current", 0))
      camera_count = int(ego_lane.get("count", 0))
      confidence = float(ego_lane.get("confidence", 0.0))
    except (TypeError, ValueError):
      return None
    if not 2 <= camera_count <= 8 or not 1 <= current <= camera_count or confidence < 0.45:
      return None

    candidates = []
    if state.get("lane_fresh", False):
      candidates.append(("current", state.get("lane_current"),
                         int(state.get("direction", 0)),
                         float(state.get("distance", -1.0)),
                         int(state.get("turn_type", -1))))
    following = state.get("next")
    if state.get("lane_ahead_fresh", False) and isinstance(following, dict) and following.get("fresh", False):
      candidates.append(("ahead", state.get("lane_ahead"),
                         int(following.get("direction", 0)),
                         float(following.get("distance", -1.0)),
                         int(following.get("turn_type", -1))))

    current_plan = None
    for source, lane, maneuver_direction, fallback_distance, turn_type in candidates:
      if not isinstance(lane, dict):
        continue
      try:
        count = int(lane.get("count", 0))
        lane_distance = float(lane.get("distance_m", fallback_distance))
      except (TypeError, ValueError):
        continue
      if count != camera_count:
        continue
      available = cls._ints(lane.get("available"), count)
      if available is None:
        continue
      recommended = [index + 1 for index, value in enumerate(available) if value != 0]
      if not recommended:
        continue
      target = min(recommended, key=lambda value: (abs(value - current), value))
      direction = -1 if target < current else 1 if target > current else 0
      # TMAP lane numbering and the camera estimate are both left-to-right.
      # Reject payloads that would prepare on the opposite side of the explicit
      # maneuver instead of guessing across lanes.
      if maneuver_direction in (-1, 1) and direction and direction != maneuver_direction:
        continue
      distance = lane_distance if lane_distance > 0.0 else fallback_distance
      plan = {"count": count, "current": current, "target": target,
              "direction": direction, "distance": distance,
              "recommended": recommended, "source": source,
              "maneuver_direction": maneuver_direction, "turn_type": turn_type}
      if direction:
        return plan
      if source == "current":
        current_plan = plan
    return current_plan

  @staticmethod
  def _event_key(state, plan):
    return (str(plan.get("source", "current")), int(plan.get("turn_type", -1)),
            int(plan.get("maneuver_direction", 0)), int(plan["count"]),
            tuple(plan["recommended"]))

  def update(self, state, ego_lane, v_ego, left_open, right_open,
             driver_cancel=False, lane_change_started=False,
             lane_change_finished=False):
    # Keep the virtual blinker asserted through both lane-change phases.  The
    # regular DesireHelper FSM needs it to finish fading lane lines back in.
    if self.requested_direction and (lane_change_started or lane_change_finished):
      if driver_cancel:
        self.canceled = True
        self.requested_direction = 0
        return 0
      self.finishing_seen = self.finishing_seen or lane_change_finished
      return self.requested_direction

    plan = self.lane_plan(state, ego_lane)
    if plan is None:
      if driver_cancel:
        self.canceled = True
      return 0

    event_key = self._event_key(state, plan)
    if event_key != self.event_key:
      self.reset()
      self.event_key = event_key

    if driver_cancel:
      self.canceled = True
    if self.canceled:
      return 0

    if self.requested_direction and self.finishing_seen:
      self.waiting_from_lane = self.current_lane
      self.waiting_frames = 1
      self.requested_direction = 0
      self.finishing_seen = False

    self.current_lane = plan["current"]
    self.target_lane = plan["target"]
    self.lane_count = plan["count"]
    if self.waiting_from_lane:
      moved = ((self.current_lane < self.waiting_from_lane) if self.target_lane < self.waiting_from_lane
               else (self.current_lane > self.waiting_from_lane))
      if moved:
        self.waiting_from_lane = 0
        self.waiting_frames = 0
        self.open_count[-1] = 0
        self.open_count[1] = 0
        self.completed = self.current_lane == self.target_lane
        return 0
      else:
        self.waiting_frames += 1
        if self.waiting_frames >= self.LANE_UPDATE_FRAMES:
          self.canceled = True
        return 0

    direction = plan["direction"]
    if direction == 0:
      self.completed = True
      return 0
    if self.completed:
      return 0

    distance = plan["distance"]
    lane_delta = abs(self.target_lane - self.current_lane)
    action_distance = min(self.MAX_DISTANCE, max(250.0, float(v_ego) * 18.0,
                                                 160.0 * lane_delta))
    if not math.isfinite(distance) or distance < self.MIN_DISTANCE or distance > action_distance:
      self.open_count[-1] = 0
      self.open_count[1] = 0
      return 0

    lane_open = left_open if direction < 0 else right_open
    self.open_count[direction] = self.open_count[direction] + 1 if lane_open else 0
    self.open_count[-direction] = 0
    if self.open_count[direction] < self.CONFIRM_FRAMES:
      return 0

    self.requested_direction = direction
    return direction
