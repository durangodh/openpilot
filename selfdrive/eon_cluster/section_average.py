"""Section-control (구간단속) average speed for the HUD when the app has none.

Enforcement divides the section length by the time taken, so the average is
the distance driven since the section was first seen divided by the elapsed
time. If navigation starts mid-section this only covers the part driven since
then. Display only; never used for speed control.
"""

SECTION_AVG_MIN_TIME_S = 3.0      # do not show a noisy value right after entry
SECTION_AVG_MAX_DT_S = 0.5        # clamp gaps (process stall, missed packets)
SECTION_RESTART_JUMP_M = 300.0    # remaining distance grew: a new section


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
    elapsed = now - self.started_at
    if elapsed < SECTION_AVG_MIN_TIME_S:
      return 0.0
    return self.distance_m / elapsed * 3.6
