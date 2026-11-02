"""Section-control (구간단속) average speed for the HUD when the app has none.

Enforcement divides the section length by the time taken, so the average is
the distance driven since the section was first seen divided by the elapsed
time. If navigation starts mid-section this only covers the part driven since
then. CruiseHelper also uses it for the section boost (구간단속 보상 가속):
section_boost_speed() gives the speed that brings the average back up to the
target over the remaining distance.
"""

SECTION_AVG_MIN_TIME_S = 3.0      # do not show a noisy value right after entry
SECTION_AVG_MAX_DT_S = 0.5        # clamp gaps (process stall, missed packets)
SECTION_RESTART_JUMP_M = 300.0    # remaining distance grew: a new section
SECTION_BOOST_MIN_TIME_S = 10.0   # need this much driving before trusting the average
SECTION_BOOST_DEADBAND_KPH = 1.0  # ignore tiny deficits (noise, rounding)


class SectionAverage(object):
  def __init__(self):
    self.reset()

  def reset(self):
    self.started_at = None
    self.last_at = None
    self.distance_m = 0.0
    self.limit = 0.0
    self.remaining_m = 0.0

  def update(self, section, v_ego, now):
    """Return the average speed in km/h, or 0 while unknown.

    section: {"distance": remaining m, "limit": km/h, "average": km/h} or None
    """
    if not isinstance(section, dict):
      self.reset()
      return 0.0
    remaining = float(section.get("distance", 0.0) or 0.0)
    limit = float(section.get("limit", 0.0) or 0.0)
    reported = float(section.get("average", 0.0) or 0.0)

    new_section = (self.started_at is None or abs(limit - self.limit) > 0.5 or
                   remaining > self.remaining_m + SECTION_RESTART_JUMP_M)
    if new_section:
      self.started_at = self.last_at = now
      self.distance_m = 0.0
    else:
      dt = min(max(now - self.last_at, 0.0), SECTION_AVG_MAX_DT_S)
      self.distance_m += max(float(v_ego), 0.0) * dt
      self.last_at = now
    self.limit = limit
    self.remaining_m = remaining

    if reported > 0.0:
      return reported
    elapsed = self.elapsed(now)
    if elapsed < SECTION_AVG_MIN_TIME_S:
      return 0.0
    return self.distance_m / elapsed * 3.6

  def elapsed(self, now):
    """Seconds since the current section was first seen (0 when none)."""
    if self.started_at is None:
      return 0.0
    return max(0.0, now - self.started_at)


def section_boost_speed(average_kph, elapsed_s, remaining_m, target_avg_kph):
  """Speed (km/h) that brings the section average up to target_avg_kph.

  Returns 0 when no boost is needed or the inputs are not trustworthy yet.
  The distance driven so far is taken as average * elapsed, so an app reported
  average and the measured one are treated the same way. The caller caps the
  result (boost limit, exit deceleration, set speed).
  """
  if average_kph <= 0.0 or target_avg_kph <= 0.0 or remaining_m <= 0.0:
    return 0.0
  if elapsed_s < SECTION_BOOST_MIN_TIME_S:
    return 0.0
  if average_kph >= target_avg_kph - SECTION_BOOST_DEADBAND_KPH:
    return 0.0
  avg = average_kph / 3.6
  target = target_avg_kph / 3.6
  driven = avg * elapsed_s
  # target = (driven + remaining) / (elapsed + remaining / v)
  time_left = (driven + remaining_m) / target - elapsed_s
  if time_left <= 0.0:
    return float("inf")  # cannot fully recover; caller clamps to the boost cap
  return remaining_m / time_left * 3.6
