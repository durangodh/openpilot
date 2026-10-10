"""Bounded stop/start handoff; never replace a planner braking request."""
from math import isfinite


DEPARTURE_WINDOW = 1.0
DEPARTURE_WAIT_MAX = 1.8  # Hyundai SCC may take ~1.4 s to release standstill
DEPARTURE_MAX_SPEED = 1.5
DEPARTURE_MIN_ACCEL = 0.15
LEAD_RELEASE_MIN_SPEED = 0.25
LEAD_RELEASE_MIN_VREL = 0.1
LEAD_JERK_MIN = 0.8


def departure_motion_valid(v_lead, v_rel, *, a_lead=None, min_speed=LEAD_RELEASE_MIN_SPEED,
                           min_vrel=LEAD_RELEASE_MIN_VREL, min_accel=None):
  """Shared finite-motion predicate for the longitudinal departure layers.

  Each layer can retain its intentionally different safety profile (for example,
  the MPC uses a stricter relative-speed threshold while the handoff additionally
  requires non-negative lead acceleration) without reimplementing comparisons.
  """
  values = [v_lead, v_rel]
  if min_accel is not None:
    values.append(a_lead if a_lead is not None else float('nan'))
  if not all(isfinite(x) for x in values):
    return False
  if min_speed is not None and v_lead <= min_speed:
    return False
  if v_rel <= min_vrel:
    return False
  return min_accel is None or (a_lead is not None and a_lead > min_accel)


def lead_is_departing(lead, *, require_radar=False, min_distance=None, max_distance=None,
                      min_speed=LEAD_RELEASE_MIN_SPEED, min_vrel=LEAD_RELEASE_MIN_VREL,
                      min_accel=None):
  """Validate a lead object and apply the common departure-motion predicate."""
  if lead is None or not getattr(lead, 'status', False):
    return False
  if require_radar and not getattr(lead, 'radar', False):
    return False
  if min_distance is not None or max_distance is not None:
    distance = getattr(lead, 'dRel', float('nan'))
    if not isfinite(distance):
      return False
    if min_distance is not None and distance < min_distance:
      return False
    if max_distance is not None and distance > max_distance:
      return False
  return departure_motion_valid(
    getattr(lead, 'vLeadK', float('nan')),
    getattr(lead, 'vRel', float('nan')),
    a_lead=getattr(lead, 'aLeadK', None),
    min_speed=min_speed, min_vrel=min_vrel, min_accel=min_accel)


# 레이더 원래 속도(vLead)는 필터 속도(vLeadK)보다 0.1~0.25초 빨리 오른다.
RAW_RELEASE_MIN_SPEED = 0.25
RAW_RELEASE_MIN_VREL = 0.1
RAW_RELEASE_MIN_FILTERED = 0.1   # 필터 속도도 움직이는 쪽이어야 한다(튀는 값 하나로 출발 방지)
CREEP_MIN_SPEED = 0.15
CREEP_MIN_VREL = 0.05


def lead_raw_departing(lead):
  """레이더로 잡은 앞차가 원래 속도로 출발 중인지(FastLeadRelease)."""
  if lead is None or not getattr(lead, 'status', False) or not getattr(lead, 'radar', False):
    return False
  v_lead_k = getattr(lead, 'vLeadK', float('nan'))
  return isfinite(v_lead_k) and v_lead_k > RAW_RELEASE_MIN_FILTERED and departure_motion_valid(
    getattr(lead, 'vLead', float('nan')), getattr(lead, 'vRel', float('nan')),
    min_speed=RAW_RELEASE_MIN_SPEED, min_vrel=RAW_RELEASE_MIN_VREL)


def lead_is_creeping(lead):
  """앞차가 막 움직이기 시작한 기미(EarlyHoldRelax). 출발 판정이 아니라 제동을 조금 줄이는 데만 쓴다."""
  if lead is None or not getattr(lead, 'status', False):
    return False
  return departure_motion_valid(
    getattr(lead, 'vLead', float('nan')), getattr(lead, 'vRel', float('nan')),
    min_speed=CREEP_MIN_SPEED, min_vrel=CREEP_MIN_VREL)


def lead_departure_jerk(lead, configured_start, desired_gap):
  """Shape launch jerk from the lead's measured departure.

  A creeping lead gets a gentle release, while a lead that is clearly pulling
  away can use the configured start jerk. The result is only an upper limit;
  planner acceleration, following caps and all stop vetoes still win.
  """
  if lead is None or not getattr(lead, 'status', False):
    return None
  values = (getattr(lead, 'dRel', float('nan')),
            getattr(lead, 'vLeadK', float('nan')),
            getattr(lead, 'vRel', float('nan')),
            getattr(lead, 'aLeadK', float('nan')), desired_gap, configured_start)
  if not all(isfinite(x) for x in values) or desired_gap <= 0.0 or configured_start <= 0.0:
    return None

  distance, v_lead, v_rel, a_lead = values[:4]
  motion = max(0.0, min(v_lead, v_rel))
  motion_weight = max(0.0, min(1.0, (motion - 0.2) / 1.3))
  accel_weight = max(-1.0, min(1.0, a_lead))
  gap_weight = max(0.0, min(1.0, (distance - desired_gap) / 3.0))
  natural_jerk = 1.4 + 2.2 * motion_weight + 0.4 * accel_weight + 0.3 * gap_weight
  return min(configured_start, max(LEAD_JERK_MIN, natural_jerk))


class LeadDepartureAssist:
  def __init__(self, dt):
    self.dt = dt
    self.remaining = 0.0
    self.waiting = 0.0
    self.active = False
    self.accel_floor = 0.0

  def reset(self):
    self.remaining = 0.0
    self.waiting = 0.0
    self.active = False
    self.accel_floor = 0.0

  def update(self, *, enabled, stopping, confirmed, cs, plan, radar,
             radar_valid, plan_valid, plan_age, a_now, a_target,
             v_target, v_future, soft_hold, fast_raw=False):
    # This is only an adapter for a positive, fresh MPC departure request.
    # No stored acceleration survives a stop/brake request or a sensor failure.
    values = (cs.vEgo, plan_age, a_now, a_target, v_target, v_future)
    safe = (enabled and plan_valid and all(isfinite(x) for x in values) and
            0.0 <= plan_age <= 0.2 and 0.0 <= cs.vEgo < DEPARTURE_MAX_SPEED and
            not cs.brakePressed and not cs.gasPressed and not soft_hold and
            not getattr(plan, 'onStop', True) and not getattr(plan, 'fcw', True) and
            int(getattr(plan, 'trafficState', 1)) % 100 != 1 and
            a_now >= 0.0 and a_target > DEPARTURE_MIN_ACCEL and
            v_target >= 0.0 and v_future > max(v_target, cs.vEgo) + 0.01 and
            radar_valid and radar is not None and not radar.radarErrors)
    lead = radar.leadOne if safe else None
    if safe:
      desired_gap = float(getattr(plan, 'desiredDistance', float('nan')))
      min_gap = max(3.5, desired_gap - 0.5)
      safe = (isfinite(desired_gap) and desired_gap > 0.0 and
              self._moving_lead(lead, min_gap, fast_raw))
      # A closer second obstacle must agree with departure too.
      second = getattr(radar, 'leadTwo', None)
      if second is not None and second.status:
        safe = safe and isfinite(second.dRel) and (
          second.dRel > lead.dRel + 2.0 or self._moving_lead(second, min_gap, fast_raw))
    if not safe:
      self.reset()
      return False

    if stopping:
      # Arm once per actual stop, using LongControl's two fresh radar samples.
      if not confirmed or cs.vEgo > 0.3:
        self.reset()
        return False
      self.remaining = DEPARTURE_WINDOW
      self.waiting = 0.0
    else:
      if cs.vEgo < 0.2:
        self.waiting += self.dt
        if self.waiting >= DEPARTURE_WAIT_MAX:
          self.reset()
          return False
      else:
        self.remaining = max(0.0, self.remaining - self.dt)

    self.active = self.remaining > 0.0
    # Unlike StarPilot's planner override, never exceed the acceleration that
    # this fork's MPC already permits (including mode and cornering limits).
    self.accel_floor = min(a_target, 0.35 if lead.vLeadK >= 0.6 else 0.18) if self.active else 0.0
    return self.active

  @staticmethod
  def _moving_lead(lead, min_gap, fast_raw=False):
    moving = lead_is_departing(lead, require_radar=True, min_distance=min_gap, max_distance=20.0)
    if not moving and fast_raw:
      # FastLeadRelease: 레이더 원래 속도로도 출발 확인(거리 조건은 같다).
      d = getattr(lead, 'dRel', float('nan'))
      moving = lead_raw_departing(lead) and isfinite(d) and min_gap <= d <= 20.0
    return moving and isfinite(lead.aLeadK) and lead.aLeadK >= 0.0


LAUNCH_JERK_UPPER_MAX = 1.8


def departure_jerk_upper(normal_upper, configured_start, pid_upper, assisted):
  """Raise LongControl's launch jerk only for a confirmed assisted launch."""
  if not assisted:
    return normal_upper
  # Retain a deliberately softer user PID setting and all existing hard caps.
  # Partially bypass the C2 startup ramp only (was 2.0); keeps a human-like launch.
  launch_upper = min(LAUNCH_JERK_UPPER_MAX, max(0.0, pid_upper), max(0.0, configured_start) * 2.0)
  return min(5.0, max(normal_upper, launch_upper))


# 주행 중 앞차 놓침 유지(radard). 가까이서 다가가던 앞차를 레이더·카메라가 잠깐 둘 다
# 놓치면, 플래너는 앞차가 없다고 보고 바로 설정속도로 가속했다(2026-10-10 long_trace
# 59.8 s 41 m, 68.5 s 16.6 m: 놓친 직후 가속 → 운전자 브레이크). 짧은 끊김 동안은
# 마지막 상대속도로 거리를 이어 계산해 같은 앞차로 유지한다.
LEAD_HOLD_S = 1.2
LEAD_HOLD_MAX_DIST = 40.0
LEAD_HOLD_MIN_CLOSING = 0.3   # m/s, 다가가는 중일 때만(멀어지는 차는 그냥 놓는다)


class LeadDropoutHold:
  def __init__(self):
    self.held = None
    self.lost_at = None
    self.last_t = None
    self.seen_at = None

  def update(self, lead, now):
    """lead: radard 의 leadOne dict. 끊김이면 이어 계산한 dict 를 돌려준다."""
    if lead.get('status', False):
      self.held = dict(lead)
      self.lost_at = None
      self.last_t = now
      self.seen_at = now
      return lead
    held = self.held
    if held is None or self.last_t is None or self.seen_at is None:
      return lead
    if self.lost_at is None:
      d0, v_rel = held.get('dRel', float('nan')), held.get('vRel', float('nan'))
      if not (isfinite(d0) and isfinite(v_rel) and 0.0 < d0 <= LEAD_HOLD_MAX_DIST and
              v_rel <= -LEAD_HOLD_MIN_CLOSING):
        self.held = None
        return lead
      self.lost_at = now
    # 마지막으로 실제로 본 때부터 LEAD_HOLD_S 까지만.
    if now - self.seen_at > LEAD_HOLD_S:
      self.held = None
      self.lost_at = None
      return lead
    dt = max(0.0, now - self.last_t)
    self.last_t = now
    held['dRel'] = max(0.5, held['dRel'] + held['vRel'] * dt)
    held['fcw'] = False
    return dict(held)

