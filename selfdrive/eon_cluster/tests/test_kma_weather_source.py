from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
JAVA = ROOT / "selfdrive/eon_cluster/android_hud/app/src/main/java/ai/comma/remotehud"


def test_weather_uses_kma_only_and_keeps_key_private():
  service = (JAVA / "WeatherService.java").read_text(encoding="utf-8")
  kma = (JAVA / "KmaWeather.java").read_text(encoding="utf-8")
  prefs = (JAVA / "AppPrefs.java").read_text(encoding="utf-8")
  activity = (JAVA / "MainActivity.java").read_text(encoding="utf-8")

  assert "getUltraSrtNcst" in kma
  assert "getVilageFcst" in kma
  assert "T1H" in kma and "REH" in kma and "WSD" in kma
  assert "KmaWeather.fetch(key, lat, lon)" in service
  assert "api.open-meteo.com" not in service + kma
  assert "air-quality-api.open-meteo.com" not in service + kma
  assert 'private static final String KMA_API_KEY = "kma_api_key"' in prefs
  assert "ACTION_OPEN_DOCUMENT" in activity
  assert "KMA_KEY_FILE_REQUEST" in activity
  assert "BuildConfig" not in service + kma


def test_kma_grid_reference_points():
  # The production Java uses the official DFS conversion constants. These
  # reference cells catch swapped latitude/longitude or rounding regressions.
  source = (JAVA / "KmaWeather.java").read_text(encoding="utf-8")
  for literal in ("6371.00877 / 5.0", "Math.toRadians(30.0)",
                  "Math.toRadians(60.0)", "Math.toRadians(126.0)",
                  "Math.toRadians(38.0)", "+ 43.0", "+ 136.0"):
    assert literal in source


if __name__ == "__main__":
  test_weather_uses_kma_only_and_keeps_key_private()
  test_kma_grid_reference_points()
  print("KMA weather source checks passed")
