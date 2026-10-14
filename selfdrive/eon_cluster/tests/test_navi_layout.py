from selfdrive.eon_cluster.navi_layout import AUTO_RELEASE_S, NaviLayout


def test_fixed_settings():
  layout = NaviLayout()
  assert layout.update(0, True, now=0.0) == 0
  assert layout.update(1, False, now=1.0) == 1


def test_auto_follows_guidance_with_release_delay():
  layout = NaviLayout()
  assert layout.update(2, False, now=0.0) == 0
  assert layout.update(2, True, now=1.0) == 1
  # 재탐색 등으로 잠깐 안내가 비어도 유지
  assert layout.update(2, False, now=1.0 + AUTO_RELEASE_S - 0.5) == 1
  assert layout.update(2, False, now=1.0 + AUTO_RELEASE_S + 0.1) == 0
  # 한 번 내려간 뒤에는 안내가 다시 올 때까지 주행모드
  assert layout.update(2, False, now=20.0) == 0


def test_read_map_scales_defaults_and_bounds():
  from selfdrive.eon_cluster.navi_layout import read_map_scales

  class FakeParams:
    def __init__(self, values):
      self.values = values

    def get(self, key):
      return self.values.get(key)

  assert read_map_scales(FakeParams({})) == {
    "hud_map_scale_tmap": 70, "hud_map_scale_kakao": 70, "hud_map_scale_naver": 70}
  scales = read_map_scales(FakeParams({
    "EonClusterHudMapScaleTmap": b"10",
    "EonClusterHudMapScaleKakao": b"250",
    "EonClusterHudMapScaleNaver": b"bad"}))
  assert scales == {"hud_map_scale_tmap": 30, "hud_map_scale_kakao": 200, "hud_map_scale_naver": 70}
