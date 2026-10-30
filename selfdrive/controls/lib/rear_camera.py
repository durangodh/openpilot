"""후면 단속카메라: 통과한 뒤에도 일정 거리 동안 카메라 제한속도를 유지한다.

후면 카메라는 지나간 차의 뒷번호판을 찍으므로 카메라 위치를 지나서도 속도를 지켜야
한다. 네이버는 코드는 일반 과속카메라(SpeedCam)와 같고 속성 목록에 RearCamera 를
넣어 구분한다(2026-10-30 09:17 실차 로그). 네이버 모듈이 sdi 에 "rear": true 를 붙이고,
NavigationRouteData.speed_events() 가 camera["rear"] 로 넘긴다.

CruiseHelper(감속)와 remote_hud_s9(HUD 표시)가 같은 규칙을 쓰도록 여기 둔다.
"""

REAR_CAMERA_HOLD_DIST = 150.0      # m, 카메라 통과 후 제한속도 유지 거리
# 카메라 직전(이 거리 안)에서 안내가 사라지면 통과한 것으로 보고, 남은 거리 + 유지거리로 잡는다.
# 더 멀리서 사라지면(경로 이탈·앱 전환) 유지하지 않는다.
REAR_CAMERA_LOST_ARM_DIST = 60.0   # m


def is_rear_camera(camera):
  return isinstance(camera, dict) and bool(camera.get("rear", False)) and \
         float(camera.get("distance", 0.0) or 0.0) > 0.0 and float(camera.get("limit", 0.0) or 0.0) > 0.0


class RearCameraHold:
  """Track an approaching rear camera and hold its limit for a distance after passing it."""
  def __init__(self):
    self.reset()

  def reset(self):
    self.remaining = 0.0
    self.limit = 0.0
    self._clear_approach()

  def _clear_approach(self):
    self.approach_limit = 0.0
    self.approach_dist = 0.0
    self.approach_raw = 0.0
    self.passed = False

  def arm(self, limit, extra=0.0):
    self.remaining = REAR_CAMERA_HOLD_DIST + max(0.0, float(extra))
    self.limit = float(limit)

  def update(self, camera, traveled):
    """camera: NavigationRouteData.speed_events()["camera"] (or None).

    Returns True while the post-camera hold is active (self.limit/remaining are valid).
    """
    traveled = max(0.0, float(traveled))
    if self.remaining > 0.0:
      self.remaining = max(0.0, self.remaining - traveled)
    if self.approach_limit > 0.0 and not self.passed:
      self.approach_dist = max(0.0, self.approach_dist - traveled)

    if is_rear_camera(camera):
      limit = float(camera["limit"])
      raw = float(camera["distance"])
      # Same new-camera rule as CruiseHelper's camera countdown.
      if self.approach_limit <= 0.0 or abs(limit - self.approach_limit) > 0.1 or raw > self.approach_raw + 100.0:
        self.approach_dist = raw
        self.passed = False
      elif not self.passed:
        self.approach_dist = min(self.approach_dist, raw)
      self.approach_raw = raw
      self.approach_limit = limit
      # The navi distance lags; start the hold when our countdown reaches the camera.
      if not self.passed and self.approach_dist <= 0.0:
        self.arm(limit)
        self.passed = True
    elif self.approach_limit > 0.0:
      # The rear camera dropped out of the guidance: passed it when it was close.
      if not self.passed and self.approach_dist <= REAR_CAMERA_LOST_ARM_DIST:
        self.arm(self.approach_limit, self.approach_dist)
      self._clear_approach()

    if self.remaining <= 0.0:
      self.limit = 0.0
    return self.remaining > 0.0
