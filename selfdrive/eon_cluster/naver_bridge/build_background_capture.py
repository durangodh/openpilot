"""Build HUD11 so Naver's attached background renderer remains capturable.

HUD10 kept HUD6's strict ``View.isShown()`` gate. That gate is false while the
Remote HUD Activity is in front, even though Naver's TextureView/SurfaceView is
still attached and has a valid buffer. Replace only the capture support classes
inside classes43.dex; keep the bridge transport and every other APK entry.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import zipfile

from build_patch import signature_entry


HUD10_SHA256 = "22f3854803dc0bcbd63d77382b5b0f3a159435d2ebc6a2d4cada44e403c410ca"
PACKAGE = Path("com/naver/map/carrot")
CAPTURE_CLASSES = ("CarrotMapCapture", "CarrotCarMapCapture", "MapCaptureGeometry", "NaverHudSettings")


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  for key in ("input", "java-home", "sdk", "apktool", "work", "output"):
    parser.add_argument("--" + key, type=Path, required=True)
  args = parser.parse_args()
  if hashlib.sha256(args.input.read_bytes()).hexdigest() != HUD10_SHA256:
    raise ValueError("Expected the published CarrotNaver HUD10 APKS")
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
  stub.write_text("package com.naver.map.carrot; class CarrotNaverBridge {"
                  " void sendBitmap(Object b) {} void clearMap() {} }", encoding="utf-8")
  classes = work / "classes"
  classes.mkdir()
  sources = [Path(__file__).with_name(name + ".java") for name in CAPTURE_CLASSES]
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
  bridge = smali / "CarrotNaverBridge.smali"
  bridge_before = bridge.read_bytes()
  for stem in CAPTURE_CLASSES:
    for item in smali.glob(stem + "*.smali"):
      item.unlink()
  for item in (work / "capture/smali" / PACKAGE).glob("*.smali"):
    shutil.copyfile(item, smali / item.name)
  assert bridge.read_bytes() == bridge_before
  apktool("b", work / "bridge", "-o", work / "bridge-patched.apk")
  with zipfile.ZipFile(work / "bridge-patched.apk") as apk:
    replacement = {"classes43.dex": apk.read("classes.dex")}

  args.output.parent.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(work / "base.apk") as original, zipfile.ZipFile(args.output, "w") as output:
    for entry in original.infolist():
      if not signature_entry(entry.filename):
        output.writestr(entry, replacement.get(entry.filename, original.read(entry)))
  with zipfile.ZipFile(work / "base.apk") as original, zipfile.ZipFile(args.output) as output:
    for entry in original.infolist():
      if entry.filename != "classes43.dex" and not signature_entry(entry.filename):
        assert original.read(entry) == output.read(entry.filename), entry.filename
    assert output.testzip() is None
  print("Verified: only classes43.dex changed; HUD10 bridge transport preserved. UNSIGNED:", args.output)


if __name__ == "__main__":
  main()
