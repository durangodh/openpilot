"""S9 HUD wire geometry: forward X, left-positive Y, original model Z.

Model/MPC Y is right-positive. Radar, vision objects, cameraGround and map
geometry already use left-positive Y. Display reflection belongs exclusively
to the renderer, after comparisons, smoothing and projection.
"""


def normalize_geometry(packet, flip):
  if packet.get("hudLateralFrame") != "left":
    def model_points(points):
      if not points:
        return points
      return [[p[0], -p[1]] + list(p[2:]) for p in points
              if isinstance(p, (list, tuple)) and len(p) >= 2]

    packet["path"] = model_points(packet.get("path"))
    for key in ("lanes", "edges"):
      for line in packet.get(key) or []:
        if isinstance(line, dict):
          line["p"] = model_points(line.get("p"))
    packet["pathOffset"] = -float(packet.get("pathOffset", 0.0) or 0.0)
    packet["hudLateralFrame"] = "left"
  packet["hudPathFlip"] = int(bool(flip))
  return packet
