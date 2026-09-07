"""Rebuild HUD3's capture bridge, preserving every other APK payload entry.

Produces an UNSIGNED base APK. Sign with HUD3's original key before installing.
Never bundle it with splits signed by a different certificate.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import zipfile

HUD3_SHA256 = "2975b6d43b1786de4864b126f44895b6d55e2c1fea448a39cc93ef15dbb64071"
PACKAGE = Path("com/naver/map/carrot")


def signature_entry(name):
  name = name.upper()
  return name.startswith("META-INF/") and (name.endswith((".SF", ".RSA", ".DSA", ".EC"))
                                           or name == "META-INF/MANIFEST.MF")


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  for name in ("input", "java-home", "sdk", "apktool", "work", "output"):
    parser.add_argument("--" + name, type=Path, required=True)
  args = parser.parse_args()
  if hashlib.sha256(args.input.read_bytes()).hexdigest() != HUD3_SHA256:
    raise ValueError("Expected the verified CarrotNaver_6.9.1.3_hud3.apks release")
  if args.output.exists():
    raise FileExistsError(args.output)
  work = args.work.resolve()
  work.mkdir(parents=True, exist_ok=False)
  suffix = ".exe" if (args.java_home / "bin/java.exe").exists() else ""
  java = args.java_home / ("bin/java" + suffix)
  javac = args.java_home / ("bin/javac" + suffix)
  android = args.sdk / "platforms/android-35/android.jar"

  def run(*command):
    subprocess.run([str(part) for part in command], check=True)

  def apktool(*command):
    run(java, "-jar", args.apktool, *command)

  with zipfile.ZipFile(args.input) as bundle:
    (work / "base.apk").write_bytes(bundle.read("base.apk"))
  with zipfile.ZipFile(work / "base.apk") as original:
    manifest = original.read("AndroidManifest.xml")
    with zipfile.ZipFile(work / "bridge.apk", "w") as bridge:
      bridge.writestr("AndroidManifest.xml", manifest)
      bridge.writestr("classes.dex", original.read("classes43.dex"))
  apktool("d", "-r", "-o", work / "bridge", work / "bridge.apk")
  stub = work / "src" / PACKAGE / "CarrotNaverBridge.java"
  stub.parent.mkdir(parents=True)
  stub.write_text("package com.naver.map.carrot; class CarrotNaverBridge { "
                  "void sendBitmap(Object bitmap) {} void clearMap() throws Exception {} }", encoding="utf-8")
  classes = work / "classes"
  classes.mkdir()
  run(javac, "--release", "8", "-encoding", "UTF-8", "-cp", android, "-d", classes,
      stub, Path(__file__).with_name("CarrotMapCapture.java"),
      Path(__file__).with_name("MapCaptureGeometry.java"),
      Path(__file__).with_name("NaverHudSettings.java"))
  with zipfile.ZipFile(work / "capture.jar", "w") as jar:
    for item in classes.rglob("*.class"):
      if item.name != "CarrotNaverBridge.class":
        jar.write(item, item.relative_to(classes).as_posix())
  dex = work / "dex"
  dex.mkdir()
  run(java, "-cp", args.sdk / "build-tools/35.0.0/lib/d8.jar", "com.android.tools.r8.D8",
      "--min-api", "26", "--lib", android, "--output", dex, work / "capture.jar")
  with zipfile.ZipFile(work / "capture.apk", "w") as capture:
    capture.writestr("AndroidManifest.xml", manifest)
    capture.write(dex / "classes.dex", "classes.dex")
  apktool("d", "-r", "-o", work / "capture", work / "capture.apk")
  smali = work / "bridge/smali" / PACKAGE
  for item in smali.glob("CarrotMapCapture*.smali"):
    item.unlink()
  for item in (work / "capture/smali" / PACKAGE).glob("*.smali"):
    shutil.copyfile(item, smali / item.name)
  bridge = smali / "CarrotNaverBridge.smali"
  source = bridge.read_text(encoding="utf-8")
  if ".method sendBitmap(Ljava/lang/Object;)V" not in source:
    raise ValueError("Unexpected bridge ABI")
  start = source.index(".method sendBitmap(Ljava/lang/Object;)V")
  end = source.index(".end method", start)
  encoder = source[start:end]
  if 'const/16 v2, 0x41' not in encoder or '\\"width\\":640,\\"height\\":384' not in encoder:
    raise ValueError("Unexpected map JPEG encoder")
  encoder = encoder.replace('const/16 v2, 0x41', 'invoke-static {}, Lcom/naver/map/carrot/NaverHudSettings;->quality()I\n\n    move-result v2')
  encoder = encoder.replace('\\"width\\":640,\\"height\\":384', '\\"width\\":960,\\"height\\":576')
  source = source[:start] + encoder + source[end:]
  source += '''
.method clearMap()V
    .locals 2
    const-string v0, "map_main"
    const/4 v1, 0x0
    invoke-direct {p0, v0, v1}, Lcom/naver/map/carrot/CarrotNaverBridge;->send(Ljava/lang/String;Ljava/lang/String;)V
    return-void
.end method
'''
  bridge.write_text(source, encoding="utf-8")
  apktool("b", work / "bridge", "-o", work / "patched-bridge.apk")
  with zipfile.ZipFile(work / "patched-bridge.apk") as patched:
    replacement = patched.read("classes.dex")
  args.output.parent.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(work / "base.apk") as original, zipfile.ZipFile(args.output, "w") as output:
    for entry in original.infolist():
      if not signature_entry(entry.filename):
        output.writestr(entry, replacement if entry.filename == "classes43.dex" else original.read(entry))
  with zipfile.ZipFile(work / "base.apk") as original, zipfile.ZipFile(args.output) as output:
    for entry in original.infolist():
      if entry.filename != "classes43.dex" and not signature_entry(entry.filename):
        assert original.read(entry) == output.read(entry.filename), entry.filename
    assert output.testzip() is None
  print("Verified: only classes43.dex changed; all other non-signature entries preserved.")
  print("UNSIGNED output:", args.output)


if __name__ == "__main__":
  main()
