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
