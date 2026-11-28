#!/usr/bin/env python3
"""
Boot animation like the comma 3: the comma logo with the spinner ring turning, on black, until Android has booted.
Makes s20/ui/bootanim/bootanimation.zip from openpilot's spinner images (git lfs). Needs Pillow.
  python3 s20/ui/bootanim/make_bootanim.py
Installed by s20/android/autostart.sh install (a Magisk module that replaces /product/media/bootanimation.zip).
"""
import io, os, zipfile
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(HERE, "../../../openpilot/selfdrive/assets/images")
SIZE, LOGO, FPS = 640, 460, 30  # frame px (drawn centred on the screen), logo px, frames per second (one turn per second)


def main():
  comma = Image.open(os.path.join(IMG, "spinner_comma.png")).convert("RGBA").resize((LOGO, LOGO), Image.LANCZOS)
  track = Image.open(os.path.join(IMG, "spinner_track.png")).convert("RGBA").resize((LOGO, LOGO), Image.LANCZOS)
  off = (SIZE - LOGO) // 2
  out = os.path.join(HERE, "bootanimation.zip")
  with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:  # the boot animation player wants an uncompressed zip
    z.writestr("desc.txt", f"{SIZE} {SIZE} {FPS}\np 0 0 part0\n")
    for i in range(FPS):
      f = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 255))
      f.alpha_composite(comma, (off, off))
      f.alpha_composite(track.rotate(-360 * i / FPS, resample=Image.BICUBIC), (off, off))
      b = io.BytesIO()
      f.convert("RGB").save(b, "PNG", optimize=True)
      z.writestr(f"part0/{i:05d}.png", b.getvalue())
  print(f"wrote {out} ({os.path.getsize(out)} bytes)")


if __name__ == "__main__":
  main()
