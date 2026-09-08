"""Patch verified HUD10 to capture Android Auto and phone map Surfaces together.

Produces an unsigned base APK. Sign with the HUD10 key and retain its splits.
Only classes5.dex (car Surface lifecycle hooks) and classes43.dex (bridge) change.
"""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

from build_patch import signature_entry

HUD10_SHA256 = "22f3854803dc0bcbd63d77382b5b0f3a159435d2ebc6a2d4cada44e403c410ca"
PACKAGE = Path("com/naver/map/carrot")
CALLBACK = Path("com/naver/map/core/auto/map/MapProvider$mapSurfaceCallback$1.smali")


def patch_callback(text):
  attach = "invoke-virtual {v2, v0, v3, v4}, Lcom/naver/maps/map/MapSurface;->q(Landroid/view/Surface;II)V"
  if text.count(attach) != 1:
    raise ValueError("Unexpected Naver car map resize ABI")
  text = text.replace(attach, attach + "\n\n    invoke-static {p0, v0, v3, v4}, Lcom/naver/map/carrot/CarrotCarMapCapture;->available(Ljava/lang/Object;Landroid/view/Surface;II)V")
  start = text.index(".method public c(Landroidx/car/app/SurfaceContainer;)V")
  end = text.index(".end method", start)
  body = text[start:end]
  if '"onSurfaceDestroyed"' not in body:
    raise ValueError("Unexpected car surface destruction ABI")
  body, count = re.subn(r"(\.locals \d+)", r"\1\n\n    invoke-static {p0}, Lcom/naver/map/carrot/CarrotCarMapCapture;->destroyed(Ljava/lang/Object;)V", body, count=1)
  if count != 1:
    raise ValueError("Missing locals directive")
  return text[:start] + body + text[end:]


def patch_bridge(text):
  start = text.index(".method private captureMap()V")
  end = text.index(".end method", start)
  body = text[start:end]
  locals_match = re.search(r"\.locals (\d+)", body)
  if not locals_match or int(locals_match[1]) < 1:
    raise ValueError("Unexpected captureMap registers")
  hook = """
    invoke-static {p0}, Lcom/naver/map/carrot/CarrotCarMapCapture;->capture(Lcom/naver/map/carrot/CarrotNaverBridge;)Z
    move-result v0
    if-eqz v0, :hud_phone_capture
    return-void
    :hud_phone_capture
"""
  body = body[:locals_match.end()] + "\n" + hook + body[locals_match.end():]
  return text[:start] + body + text[end:]


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  for key in ("input", "java-home", "sdk", "apktool", "work", "output"):
    parser.add_argument("--" + key, type=Path, required=True)
  args = parser.parse_args()
  if hashlib.sha256(args.input.read_bytes()).hexdigest() != HUD10_SHA256:
    raise ValueError("Expected published HUD10 APKS")
  if args.output.exists():
    raise FileExistsError(args.output)
  work = args.work.resolve()
  work.mkdir(parents=True, exist_ok=False)
  suffix = ".exe" if (args.java_home / "bin/java.exe").exists() else ""
  java = args.java_home / ("bin/java" + suffix)
  javac = args.java_home / ("bin/javac" + suffix)
  android = args.sdk / "platforms/android-35/android.jar"

  def run(*cmd):
    subprocess.run(list(map(str, cmd)), check=True)

  def apktool(*cmd):
    run(java, "-jar", args.apktool, *cmd, "-p", work / "framework")

  with zipfile.ZipFile(args.input) as bundle:
    base = bundle.read("base.apk")
  (work / "base.apk").write_bytes(base)
  with zipfile.ZipFile(work / "base.apk") as original:
    manifest = original.read("AndroidManifest.xml")
    for label, dex in (("bridge", "classes43.dex"), ("car", "classes5.dex")):
      with zipfile.ZipFile(work / (label + ".apk"), "w") as apk:
        apk.writestr("AndroidManifest.xml", manifest)
        apk.writestr("classes.dex", original.read(dex))
      apktool("d", "-r", "-o", work / label, work / (label + ".apk"))
  stub = work / "src" / PACKAGE / "CarrotNaverBridge.java"
  stub.parent.mkdir(parents=True)
  stub.write_text("package com.naver.map.carrot; class CarrotNaverBridge { void sendBitmap(Object b) {} void clearMap() {} }", encoding="utf-8")
  classes = work / "classes"
  classes.mkdir()
  sources = [Path(__file__).with_name(name + ".java") for name in
             ("CarrotMapCapture", "CarrotCarMapCapture", "MapCaptureGeometry", "NaverHudSettings")]
  run(javac, "--release", "8", "-encoding", "UTF-8", "-cp", android, "-d", classes, stub, *sources)
  with zipfile.ZipFile(work / "capture.jar", "w") as jar:
    for item in classes.rglob("*.class"):
      if item.name != "CarrotNaverBridge.class":
        jar.write(item, item.relative_to(classes).as_posix())
  (work / "dex").mkdir()
  run(java, "-cp", args.sdk / "build-tools/35.0.0/lib/d8.jar", "com.android.tools.r8.D8",
      "--min-api", "26", "--lib", android, "--output", work / "dex", work / "capture.jar")
  with zipfile.ZipFile(work / "capture.apk", "w") as apk:
    apk.writestr("AndroidManifest.xml", manifest)
    apk.write(work / "dex/classes.dex", "classes.dex")
  apktool("d", "-r", "-o", work / "capture", work / "capture.apk")
  smali = work / "bridge/smali" / PACKAGE
  for stem in ("CarrotMapCapture", "CarrotCarMapCapture", "MapCaptureGeometry", "NaverHudSettings"):
    for item in smali.glob(stem + "*.smali"):
      item.unlink()
  for item in (work / "capture/smali" / PACKAGE).glob("*.smali"):
    shutil.copyfile(item, smali / item.name)
  bridge = smali / "CarrotNaverBridge.smali"
  bridge.write_text(patch_bridge(bridge.read_text(encoding="utf-8")), encoding="utf-8")
  callback = work / "car/smali" / CALLBACK
  callback.write_text(patch_callback(callback.read_text(encoding="utf-8")), encoding="utf-8")
  replacement = {}
  for label, dex in (("bridge", "classes43.dex"), ("car", "classes5.dex")):
    apktool("b", work / label, "-o", work / (label + "-patched.apk"))
    with zipfile.ZipFile(work / (label + "-patched.apk")) as apk:
      replacement[dex] = apk.read("classes.dex")
  args.output.parent.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(work / "base.apk") as original, zipfile.ZipFile(args.output, "w") as output:
    for entry in original.infolist():
      if not signature_entry(entry.filename):
        output.writestr(entry, replacement.get(entry.filename, original.read(entry)))
  with zipfile.ZipFile(work / "base.apk") as original, zipfile.ZipFile(args.output) as output:
    for entry in original.infolist():
      if entry.filename not in replacement and not signature_entry(entry.filename):
        assert original.read(entry) == output.read(entry.filename), entry.filename
    assert output.testzip() is None
  print("Verified: only classes5.dex and classes43.dex changed. UNSIGNED:", args.output)


if __name__ == "__main__":
  main()
