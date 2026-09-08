"""Build HUD10 from the user's working HUD6 baseline; change guidance codes only.

Keep all map capture methods/classes and classes5.dex from the verified HUD6
release. This avoids the car-surface gates still present in HUD7/8/9.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import zipfile
import subprocess
from build_patch import signature_entry
from build_offscreen_map import patch_codes
HUD6_SHA256 = "52e04217403f941b281aae71f3d8f0ccbd26accb6f1f3308cba196b3b764c3c0"
PACKAGE = Path("com/naver/map/carrot")

def main():
  parser = argparse.ArgumentParser(description=__doc__)
  for key in ("input", "java-home", "sdk", "apktool", "work", "output"):
    parser.add_argument("--" + key, type=Path, required=True)
  args = parser.parse_args()
  if hashlib.sha256(args.input.read_bytes()).hexdigest() != HUD6_SHA256:
    raise ValueError("Expected verified HUD6 APKS")
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
    with zipfile.ZipFile(work / "bridge.apk", "w") as apk:
      apk.writestr("AndroidManifest.xml", manifest)
      apk.writestr("classes.dex", original.read("classes43.dex"))
  apktool("d", "-r", "-o", work / "bridge", work / "bridge.apk")
  stub = work / "src" / PACKAGE / "CarrotNaverBridge.java"
  stub.parent.mkdir(parents=True)
  stub.write_text("package com.naver.map.carrot; class CarrotNaverBridge { private static volatile Object store;"
                  " void sendBitmap(Object b) {} void clearMap() {} }", encoding="utf-8")
  classes = work / "classes"
  classes.mkdir()
  run(javac, "--release", "8", "-encoding", "UTF-8", "-cp", android, "-d", classes, stub,
      Path(__file__).with_name("CarrotNaverCodes.java"))
  with zipfile.ZipFile(work / "offscreen.jar", "w") as jar:
    for item in classes.rglob("*.class"):
      if item.name != "CarrotNaverBridge.class":
        jar.write(item, item.relative_to(classes).as_posix())
  (work / "dex").mkdir()
  run(java, "-cp", args.sdk / "build-tools/35.0.0/lib/d8.jar", "com.android.tools.r8.D8",
      "--min-api", "26", "--lib", android, "--output", work / "dex", work / "offscreen.jar")
  with zipfile.ZipFile(work / "offscreen.apk", "w") as apk:
    apk.writestr("AndroidManifest.xml", manifest)
    apk.write(work / "dex/classes.dex", "classes.dex")
  apktool("d", "-r", "-o", work / "offscreen", work / "offscreen.apk")
  smali = work / "bridge/smali" / PACKAGE
  for pattern in ("CarrotNaverCodes*.smali",):
    for item in (work / "offscreen/smali" / PACKAGE).glob(pattern):
      shutil.copyfile(item, smali / item.name)
  bridge = smali / "CarrotNaverBridge.smali"
  before = bridge.read_text(encoding="utf-8")
  after = patch_codes(before)
  # All non-guidance methods, including captureMap and sendBitmap, stay exact.
  import re
  def unchanged_methods(text):
    from build_offscreen_map import DELEGATES
    return {m.group(1): m.group(0) for m in re.finditer(r"(?ms)^\.method ([^\n]+)\n.*?^\.end method", text)
            if m.group(1) not in DELEGATES}
  assert unchanged_methods(before) == unchanged_methods(after)
  for item in smali.glob("*.smali"):
    assert "CarrotCarMapCapture" not in item.read_text(), item
    assert "CarrotOffscreenMap" not in item.read_text(), item
  bridge.write_text(after, encoding="utf-8")
  apktool("b", work / "bridge", "-o", work / "bridge-patched.apk")
  with zipfile.ZipFile(work / "bridge-patched.apk") as apk:
    replacement = {"classes43.dex": apk.read("classes.dex")}
  args.output.parent.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(work / "base.apk") as original, zipfile.ZipFile(args.output, "w") as output:
    for entry in original.infolist():
      if signature_entry(entry.filename):
        continue
      info = zipfile.ZipInfo(entry.filename, date_time=entry.date_time)
      info.compress_type = entry.compress_type
      info.external_attr = entry.external_attr
      output.writestr(info, replacement.get(entry.filename, original.read(entry)))
  with zipfile.ZipFile(work / "base.apk") as original, zipfile.ZipFile(args.output) as output:
    for entry in original.infolist():
      if entry.filename not in replacement and not signature_entry(entry.filename):
        assert original.read(entry) == output.read(entry.filename), entry.filename
    assert output.testzip() is None
  print("Verified: only classes43.dex changed. UNSIGNED:", args.output)


if __name__ == "__main__":
  main()
