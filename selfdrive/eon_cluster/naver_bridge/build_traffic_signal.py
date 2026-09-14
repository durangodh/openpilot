"""Add native traffic-signal PNG publishing to the verified HUD13.6 APKS."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import zipfile

from build_car_snapshot import decode, build, patch_signal_view
from build_patch import signature_entry


HUD136_SHA256 = "8c0b7c14b5ae78f9a74ad3b52e0d5d62e9343a17b0dfc65137c537f33310c0cf"
PACKAGE = Path("com/naver/map/carrot")


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  for key in ("input", "java-home", "sdk", "apktool", "work", "output"):
    parser.add_argument("--" + key, type=Path, required=True)
  args = parser.parse_args()
  if hashlib.sha256(args.input.read_bytes()).hexdigest() != HUD136_SHA256:
    raise ValueError("Expected verified HUD13.6 APKS")
  if args.output.exists():
    raise FileExistsError(args.output)
  work = args.work.resolve()
  work.mkdir(parents=True, exist_ok=False)
  suffix = ".exe" if (args.java_home / "bin/java.exe").exists() else ""
  java = args.java_home / ("bin/java" + suffix)
  javac = args.java_home / ("bin/javac" + suffix)
  android = args.sdk / "platforms/android-35/android.jar"
  here = Path(__file__).resolve().parent

  with zipfile.ZipFile(args.input) as bundle:
    (work / "base.apk").write_bytes(bundle.read("base.apk"))
  with zipfile.ZipFile(work / "base.apk") as original:
    manifest = original.read("AndroidManifest.xml")
    bridge_dex = original.read("classes43.dex")
    signal_dex = original.read("classes12.dex")

  stub = work / "src" / PACKAGE / "CarrotNaverBridge.java"
  stub.parent.mkdir(parents=True)
  stub.write_text("package com.naver.map.carrot; class CarrotNaverBridge { void sendBitmap(Object b) {} void clearMap() {} }",
                  encoding="utf-8")
  classes = work / "classes"
  classes.mkdir()
  subprocess.run([str(javac), "--release", "8", "-encoding", "UTF-8", "-cp", str(android), "-d", str(classes),
                  str(stub), str(here / "CarrotCarMapSnapshot.java"), str(here / "CarrotTrafficSignalCapture.java"),
                  str(here / "CarrotHudLog.java"), str(here / "CarrotNaverCodes.java")], check=True)
  with zipfile.ZipFile(work / "new.jar", "w") as jar:
    for item in classes.rglob("*.class"):
      if item.name != "CarrotNaverBridge.class":
        jar.write(item, item.relative_to(classes).as_posix())
  (work / "dex").mkdir()
  subprocess.run([str(java), "-cp", str(args.sdk / "build-tools/35.0.0/lib/d8.jar"), "com.android.tools.r8.D8",
                  "--min-api", "26", "--lib", str(android), "--output", str(work / "dex"), str(work / "new.jar")], check=True)

  bridge = decode(java, args.apktool, work, "bridge", manifest, bridge_dex)
  new = decode(java, args.apktool, work, "new", manifest, (work / "dex/classes.dex").read_bytes())
  signal = decode(java, args.apktool, work, "signal", manifest, signal_dex)
  for item in (new / "smali" / PACKAGE).glob("Carrot*.smali"):
    shutil.copyfile(item, bridge / "smali" / PACKAGE / item.name)
  signal_smali = signal / "smali/com/naver/map/core/navigation/view/NaviTrafficSignalView.smali"
  signal_smali.write_text(patch_signal_view(signal_smali.read_text(encoding="utf-8")), encoding="utf-8")
  replacement = {"classes43.dex": build(java, args.apktool, work, "bridge"),
                 "classes12.dex": build(java, args.apktool, work, "signal")}

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
  print("Verified: only classes12.dex and classes43.dex changed. UNSIGNED:", args.output)


if __name__ == "__main__":
  main()
