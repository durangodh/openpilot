package ai.s20.ui;

/** Maps road-camera frame pixels (1928x1208, image at (4,64) size 1920x1080) to the screen, cropping to fill it. */
final class Geometry {
  static final int FW = 1928, FH = 1208, IX = 4, IY = 64, IW = 1920, IH = 1080;
  float sx, sy, sw, sh, scale;  // visible frame rect and screen px per frame px

  void update(int screenW, int screenH) {
    float a = screenW / (float) screenH;
    if (IW / (float) IH < a) { sw = IW; sh = IW / a; } else { sh = IH; sw = IH * a; }
    sx = IX + (IW - sw) / 2f; sy = IY + (IH - sh) / 2f;
    scale = screenW / sw;
  }
  float x(float fx) { return (fx - sx) * scale; }
  float y(float fy) { return (fy - sy) * scale; }
}
