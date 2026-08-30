#!/usr/bin/env python3
"""Pack WGS84 GeoJSON or VWorld/NGII SHP ZIPs into hud_map.sqlite.

The Android HUD reads only the current z16 3x3 neighborhood. This converter
uses the Python standard library and reads the projection from each PRJ file.
"""

import argparse
import json
import math
import os
import re
import sqlite3
import struct
import zipfile

ZOOM = 16
MAX_TILES_PER_FEATURE = 64


def tile_xy(lat, lon, zoom=ZOOM):
  scale = 1 << zoom
  x = int(math.floor((lon + 180.0) / 360.0 * scale))
  radians = math.radians(max(-85.05112878, min(85.05112878, lat)))
  y = int(math.floor((1.0 - math.log(math.tan(radians) + 1.0 / math.cos(radians))
                      / math.pi) * 0.5 * scale))
  return max(0, min(scale - 1, x)), max(0, min(scale - 1, y))


def _wkt_number(wkt, name, default=None):
  match = re.search(r'PARAMETER\["%s",\s*([-+0-9.eE]+)\]' % re.escape(name),
                    wkt, re.IGNORECASE)
  if match:
    return float(match.group(1))
  if default is not None:
    return default
  raise ValueError("PRJ is missing %s" % name)


def _tm_parameters(wkt):
  spheroid = re.search(r'SPHEROID\["[^"]+",\s*([-+0-9.eE]+),\s*([-+0-9.eE]+)',
                       wkt, re.IGNORECASE)
  if "Transverse_Mercator" not in wkt or not spheroid:
    raise ValueError("only Transverse_Mercator SHP projections are supported")
  a = float(spheroid.group(1))
  f = 1.0 / float(spheroid.group(2))
  return (a, f * (2.0 - f), _wkt_number(wkt, "False_Easting", 0.0),
          _wkt_number(wkt, "False_Northing", 0.0),
          math.radians(_wkt_number(wkt, "Central_Meridian")),
          _wkt_number(wkt, "Scale_Factor", 1.0),
          math.radians(_wkt_number(wkt, "Latitude_Of_Origin", 0.0)))


def _meridian_arc(latitude, a, e2):
  e4, e6 = e2 * e2, e2 * e2 * e2
  return a * ((1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256) * latitude
              - (3 * e2 / 8 + 3 * e4 / 32 + 45 * e6 / 1024) * math.sin(2 * latitude)
              + (15 * e4 / 256 + 45 * e6 / 1024) * math.sin(4 * latitude)
              - 35 * e6 / 3072 * math.sin(6 * latitude))


def inverse_transverse_mercator(wkt):
  a, e2, fe, fn, lon0, scale, lat0 = _tm_parameters(wkt)
  ep2 = e2 / (1 - e2)
  e4, e6 = e2 * e2, e2 * e2 * e2
  factor = a * (1 - e2 / 4 - 3 * e4 / 64 - 5 * e6 / 256)
  origin = _meridian_arc(lat0, a, e2)
  e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))

  def convert(easting, northing):
    x = easting - fe
    mu = (origin + (northing - fn) / scale) / factor
    phi = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
           + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
           + 151 * e1 ** 3 / 96 * math.sin(6 * mu)
           + 1097 * e1 ** 4 / 512 * math.sin(8 * mu))
    sinp, cosp, tanp = math.sin(phi), math.cos(phi), math.tan(phi)
    n = a / math.sqrt(1 - e2 * sinp * sinp)
    r = a * (1 - e2) / (1 - e2 * sinp * sinp) ** 1.5
    t, c, d = tanp * tanp, ep2 * cosp * cosp, x / (n * scale)
    lat = phi - n * tanp / r * (d * d / 2
      - (5 + 3 * t + 10 * c - 4 * c * c - 9 * ep2) * d ** 4 / 24
      + (61 + 90 * t + 298 * c + 45 * t * t - 252 * ep2 - 3 * c * c) * d ** 6 / 720)
    lon = lon0 + (d - (1 + 2 * t + c) * d ** 3 / 6
      + (5 - 2 * c + 28 * t - 3 * c * c + 8 * ep2 + 24 * t * t) * d ** 5 / 120) / cosp
    return math.degrees(lon), math.degrees(lat)
  return convert


def forward_transverse_mercator(wkt):
  a, e2, fe, fn, lon0, scale, lat0 = _tm_parameters(wkt)
  ep2 = e2 / (1 - e2)
  origin = _meridian_arc(lat0, a, e2)

  def convert(longitude, latitude):
    lon, lat = math.radians(longitude), math.radians(latitude)
    sinp, cosp, tanp = math.sin(lat), math.cos(lat), math.tan(lat)
    n = a / math.sqrt(1 - e2 * sinp * sinp)
    t, c, aa = tanp * tanp, ep2 * cosp * cosp, (lon - lon0) * cosp
    x = fe + scale * n * (aa + (1 - t + c) * aa ** 3 / 6
                          + (5 - 18 * t + t * t + 72 * c - 58 * ep2) * aa ** 5 / 120)
    y = fn + scale * (_meridian_arc(lat, a, e2) - origin + n * tanp * (
      aa * aa / 2 + (5 - t + 9 * c + 4 * c * c) * aa ** 4 / 24
      + (61 - 58 * t + t * t + 600 * c - 330 * ep2) * aa ** 6 / 720))
    return x, y
  return convert


def _dbf_records(stream, encoding):
  header = stream.read(32)
  count = struct.unpack("<I", header[4:8])[0]
  header_length = struct.unpack("<H", header[8:10])[0]
  record_length = struct.unpack("<H", header[10:12])[0]
  descriptors = stream.read(header_length - 32)
  fields, offset = [], 1
  for position in range(0, len(descriptors) - 1, 32):
    field = descriptors[position:position + 32]
    if not field or field[0] == 0x0D:
      break
    name = field[:11].split(b"\0", 1)[0].decode("ascii", "replace")
    fields.append((name, offset, field[16]))
    offset += field[16]
  for _ in range(count):
    record = stream.read(record_length)
    if len(record) != record_length:
      raise ValueError("truncated DBF")
    if record[:1] == b"*":
      yield None
      continue
    result = {}
    for name, start, length in fields:
      raw = record[start:start + length].rstrip(b" \0")
      if raw:
        result[name] = raw.decode(encoding, "replace")
    yield result


def _signed_area(points):
  return 0.5 * sum(points[i][0] * points[(i + 1) % len(points)][1]
                   - points[(i + 1) % len(points)][0] * points[i][1]
                   for i in range(len(points))) if len(points) >= 3 else 0.0


def _shp_geometries(stream, convert, source_clip=None):
  header = stream.read(100)
  if len(header) != 100 or struct.unpack(">i", header[:4])[0] != 9994:
    raise ValueError("invalid SHP")
  while True:
    record_header = stream.read(8)
    if not record_header:
      return
    size = struct.unpack(">i", record_header[4:8])[0] * 2
    content = stream.read(size)
    shape_type = struct.unpack("<i", content[:4])[0]
    if shape_type == 0:
      yield None
      continue
    if shape_type not in (3, 5):
      raise ValueError("unsupported SHP type %d" % shape_type)
    bbox = struct.unpack("<4d", content[4:36])
    if source_clip and (bbox[2] < source_clip[0] or bbox[0] > source_clip[2]
                        or bbox[3] < source_clip[1] or bbox[1] > source_clip[3]):
      yield None
      continue
    part_count, point_count = struct.unpack("<2i", content[36:44])
    point_offset = 44 + part_count * 4
    parts = list(struct.unpack("<%di" % part_count, content[44:point_offset])) + [point_count]
    points = [convert(*struct.unpack("<2d", content[point_offset + i * 16:point_offset + (i + 1) * 16]))
              for i in range(point_count)]
    groups = [points[parts[i]:parts[i + 1]] for i in range(part_count)]
    if shape_type == 5:
      outer = [ring for ring in groups if _signed_area(ring) < 0]
      if not outer and groups:
        outer = [max(groups, key=lambda ring: abs(_signed_area(ring)))]
      yield {"type": "MultiPolygon", "coordinates": [[ring] for ring in outer]}
    else:
      yield {"type": "MultiLineString", "coordinates": groups}


def shapefile_zip_features(path, clip_bounds=None):
  with zipfile.ZipFile(path) as archive:
    names = {name.lower(): name for name in archive.namelist()}
    for shp_name in [name for name in archive.namelist() if name.lower().endswith(".shp")]:
      base = shp_name[:-4]
      dbf_name, prj_name = names.get((base + ".dbf").lower()), names.get((base + ".prj").lower())
      if not dbf_name or not prj_name:
        raise ValueError("SHP missing DBF/PRJ: %s" % shp_name)
      wkt = archive.read(prj_name).decode("ascii", "replace")
      inverse = inverse_transverse_mercator(wkt)
      source_clip = None
      if clip_bounds:
        project = forward_transverse_mercator(wkt)
        west, south, east, north = clip_bounds
        corners = [project(west, south), project(west, north), project(east, south), project(east, north)]
        source_clip = (min(p[0] for p in corners), min(p[1] for p in corners),
                       max(p[0] for p in corners), max(p[1] for p in corners))
      cpg = names.get((base + ".cpg").lower())
      encoding = archive.read(cpg).decode("ascii", "replace").strip() if cpg else "euc-kr"
      with archive.open(shp_name) as shp, archive.open(dbf_name) as dbf:
        for index, (geometry, properties) in enumerate(zip(
            _shp_geometries(shp, inverse, source_clip), _dbf_records(dbf, encoding))):
          if geometry is not None and properties is not None:
            yield {"type": "Feature", "id": properties.get("UFID", index),
                   "properties": properties, "geometry": geometry}


def geojson_features(path):
  if not path:
    return []
  with open(path, encoding="utf-8") as stream:
    root = json.load(stream)
  if root.get("type") == "FeatureCollection":
    return root.get("features") or []
  if root.get("type") == "Feature":
    return [root]
  raise ValueError("GeoJSON must contain features")


def sources(path, archives, clip=None):
  if path:
    yield geojson_features(path)
  for archive in archives or []:
    yield shapefile_zip_features(archive, clip)


def geometry_parts(geometry, category):
  kind, coordinates = (geometry or {}).get("type"), (geometry or {}).get("coordinates") or []
  if category == "b":
    if kind == "Polygon": return [coordinates[0]] if coordinates else []
    if kind == "MultiPolygon": return [polygon[0] for polygon in coordinates if polygon]
  else:
    if kind == "LineString": return [coordinates]
    if kind == "MultiLineString": return coordinates
  return []


def clean_points(points, minimum):
  result = []
  for point in points:
    try:
      lon, lat = float(point[0]), float(point[1])
    except (TypeError, ValueError, IndexError):
      continue
    pair = [round(lat, 7), round(lon, 7)]
    if math.isfinite(lat) and math.isfinite(lon) and (not result or pair != result[-1]):
      result.append(pair)
  if len(result) >= 2 and result[0] == result[-1]:
    result.pop()
  return result if len(result) >= minimum else []


def number(properties, keys, default, minimum, maximum, multiplier=1.0):
  for key in keys:
    try:
      value = float(properties.get(key)) * multiplier
      if value > 0:
        return round(max(minimum, min(maximum, value)), 1)
    except (TypeError, ValueError):
      pass
  return default


def covered_tiles(points):
  xy = [tile_xy(lat, lon) for lat, lon in points]
  x0, x1 = min(p[0] for p in xy), max(p[0] for p in xy)
  y0, y1 = min(p[1] for p in xy), max(p[1] for p in xy)
  if (x1 - x0 + 1) * (y1 - y0 + 1) > MAX_TILES_PER_FEATURE:
    return [tile_xy(sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points))]
  return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]


def build(buildings, roads, output, building_zips=None, road_zips=None):
  tiles, counts = {}, {"b": 0, "r": 0}
  bounds = [180.0, 90.0, -180.0, -90.0]
  for source in sources(buildings, building_zips):
    for index, feature in enumerate(source):
      props = feature.get("properties") or {}
      height = number(props, ("height", "HEIGHT", "건물높이"), 0, 4, 18)
      if not height:
        height = number(props, ("building:levels", "levels", "층수", "GRND_FLR"), 8, 4, 18, 3)
      for part, ring in enumerate(geometry_parts(feature.get("geometry"), "b")):
        points = clean_points(ring, 3)
        if not points: continue
        item = {"i": "b%s:%s" % (feature.get("id", index), part), "h": height, "p": points}
        for x, y in covered_tiles(points):
          tiles.setdefault((ZOOM, x, y), {"b": [], "r": []})["b"].append(item)
        counts["b"] += 1
        bounds[0] = min(bounds[0], *(p[1] for p in points)); bounds[2] = max(bounds[2], *(p[1] for p in points))
        bounds[1] = min(bounds[1], *(p[0] for p in points)); bounds[3] = max(bounds[3], *(p[0] for p in points))
  clip = None if not counts["b"] else (bounds[0] - .01, bounds[1] - .01, bounds[2] + .01, bounds[3] + .01)
  for source in sources(roads, road_zips, clip):
    for index, feature in enumerate(source):
      props = feature.get("properties") or {}
      width = number(props, ("width", "WIDTH", "도로폭", "RVWD"), 0, 2.5, 18)
      if not width:
        width = number(props, ("lanes", "RDLN"), 5.5, 2.5, 18, 3.25)
      for part, line in enumerate(geometry_parts(feature.get("geometry"), "r")):
        points = clean_points(line, 2)
        if not points: continue
        item = {"i": "r%s:%s" % (feature.get("id", index), part), "w": width, "p": points}
        for x, y in covered_tiles(points):
          tiles.setdefault((ZOOM, x, y), {"b": [], "r": []})["r"].append(item)
        counts["r"] += 1
  if os.path.exists(output): os.remove(output)
  with sqlite3.connect(output) as db:
    db.execute("CREATE TABLE tiles(z INTEGER,x INTEGER,y INTEGER,payload TEXT,PRIMARY KEY(z,x,y))")
    db.executemany("INSERT INTO tiles VALUES(?,?,?,?)", [(z, x, y, json.dumps(value, separators=(",", ":")))
                                                         for (z, x, y), value in sorted(tiles.items())])
    db.execute("CREATE TABLE metadata(name TEXT PRIMARY KEY,value TEXT NOT NULL)")
    db.executemany("INSERT INTO metadata VALUES(?,?)", [("format", "remote-hud-json-v1"),
      ("zoom", str(ZOOM)), ("tile_count", str(len(tiles))),
      ("building_count", str(counts["b"])), ("road_count", str(counts["r"]))])
  return len(tiles)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--buildings")
  parser.add_argument("--roads")
  parser.add_argument("--building-shp-zip", action="append", default=[])
  parser.add_argument("--road-shp-zip", action="append", default=[])
  parser.add_argument("--output", required=True)
  args = parser.parse_args()
  if not (args.buildings or args.roads or args.building_shp_zip or args.road_shp_zip):
    parser.error("at least one input is required")
  print("wrote %d tiles to %s" % build(args.buildings, args.roads, args.output,
                                        args.building_shp_zip, args.road_shp_zip))


if __name__ == "__main__":
  main()
