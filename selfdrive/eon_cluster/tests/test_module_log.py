import os

from selfdrive.eon_cluster import hud_stats


def test_module_log_written_for_known_source(tmp_path, monkeypatch):
  monkeypatch.setattr(hud_stats, "STATS_DIR", str(tmp_path))
  hud_stats.append_module_log("kakao", "voice: a11y find by id \"x\"\nsecond")
  files = os.listdir(tmp_path)
  assert len(files) == 1 and files[0].startswith("kakao_") and files[0].endswith(".log")
  text = (tmp_path / files[0]).read_text()
  assert "voice: a11y find by id" in text and text.count("\n") == 1


def test_module_log_ignores_unknown_source_and_non_text(tmp_path, monkeypatch):
  monkeypatch.setattr(hud_stats, "STATS_DIR", str(tmp_path))
  hud_stats.append_module_log("../etc", "x")
  hud_stats.append_module_log("kakao", {"a": 1})
  assert os.listdir(tmp_path) == []
