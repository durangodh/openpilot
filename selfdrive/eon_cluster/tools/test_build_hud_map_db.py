import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("build_hud_map_db.py")
SPEC = importlib.util.spec_from_file_location("build_hud_map_db", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BuildHudMapDbTest(unittest.TestCase):
  def test_korea_tm_round_trip(self):
    wkt = ('PROJCS["Korea",GEOGCS["Korea",DATUM["Korea",'
           'SPHEROID["GRS_1980",6378137,298.257222101]]],'
           'PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",200000],'
           'PARAMETER["False_Northing",600000],PARAMETER["Central_Meridian",127],'
           'PARAMETER["Scale_Factor",1],PARAMETER["Latitude_Of_Origin",38]]')
    forward = MODULE.forward_transverse_mercator(wkt)
    inverse = MODULE.inverse_transverse_mercator(wkt)
    easting, northing = forward(127.069, 37.149)
    lon, lat = inverse(easting, northing)
    self.assertAlmostEqual(lon, 127.069, places=6)
    self.assertAlmostEqual(lat, 37.149, places=6)

  def test_compact_tile_payload(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      buildings, roads, output = root / "b.json", root / "r.json", root / "map.sqlite"
      buildings.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "id": "b1", "properties": {"HEIGHT": 11},
        "geometry": {"type": "Polygon", "coordinates": [[
          [127.069, 37.149], [127.0691, 37.149], [127.0691, 37.1491],
          [127.069, 37.1491], [127.069, 37.149]]]}}]}))
      roads.write_text(json.dumps({"type": "FeatureCollection", "features": [{
        "type": "Feature", "id": "r1", "properties": {"lanes": 2},
        "geometry": {"type": "LineString", "coordinates": [
          [127.0688, 37.1488], [127.0693, 37.1493]]}}]}))
      self.assertGreater(MODULE.build(str(buildings), str(roads), str(output)), 0)
      with sqlite3.connect(output) as db:
        payloads = [json.loads(row[0]) for row in db.execute("SELECT payload FROM tiles")]
        metadata = dict(db.execute("SELECT name,value FROM metadata"))
      self.assertTrue(any(value["b"] for value in payloads))
      self.assertTrue(any(value["r"] for value in payloads))
      self.assertEqual(metadata["building_count"], "1")
      self.assertEqual(metadata["road_count"], "1")


if __name__ == "__main__":
  unittest.main()
