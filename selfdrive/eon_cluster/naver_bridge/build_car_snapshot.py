"""HUD13: map_main from the Android Auto NaverMap via the SDK's own takeSnapshot.

Input: verified HUD11 APKS. Output: unsigned base APK where exactly two entries
change:
  classes.dex      LocationManager$Companion.b keeps the real provider name for mock fixes
  classes43.dex  + CarrotCarMapSnapshot, CarrotHudLog;
                   CarrotNaverBridge.captureMap() tries the snapshot first
  classes5.dex     MapProvider.<init> hands its instance to CarrotCarMapSnapshot;
                   mapmatching LocationExtensionsKt.a (isMock) always false
"""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

from build_patch import signature_entry

HUD11_SHA256 = "00306aafdfc58a5ae0708dd34260fd455d52c4fd9c09c1fb4beeb730b97d99a4"
PACKAGE = Path("com/naver/map/carrot")
BRIDGE_HOOK = """

    invoke-static {p0}, Lcom/naver/map/carrot/CarrotCarMapSnapshot;->capture(Lcom/naver/map/carrot/CarrotNaverBridge;)Z

    move-result v0

    if-eqz v0, :hud_snapshot_fallback

    return-void

    :hud_snapshot_fallback"""
PROVIDER_HOOK = "    invoke-static {p0}, Lcom/naver/map/carrot/CarrotCarMapSnapshot;->provider(Ljava/lang/Object;)V\n\n    return-void"


def patch_bridge(text):
  start = text.index(".method private captureMap()V")
  end = text.index(".end method", start)
  body = text[start:end]
  if "CarrotCarMapSnapshot" in body:
    raise ValueError("captureMap already patched")
  locals_match = re.search(r"\.locals (\d+)", body)
  if not locals_match or int(locals_match[1]) < 1:
    raise ValueError("Unexpected captureMap registers")
  body = body[:locals_match.end()] + BRIDGE_HOOK + body[locals_match.end():]
  return text[:start] + body + text[end:]


def patch_set_activity(text):
  start = text.index(".method public static setActivity(Ljava/lang/Object;)V")
  end = text.index(".end method", start)
  body = text[start:end]
  if "registerActivity" in body:
    raise ValueError("setActivity already patched")
  locals_match = re.search(r"\.locals (\d+)", body)
  if not locals_match:
    raise ValueError("Unexpected setActivity")
  hook = "\n\n    invoke-static {p0}, Lcom/naver/map/carrot/CarrotCarMapSnapshot;->registerActivity(Ljava/lang/Object;)V"
  body = body[:locals_match.end()] + hook + body[locals_match.end():]
  return text[:start] + body + text[end:]


def replace_method_body(text, signature, new_body):
  start = text.index(signature)
  end = text.index(".end method", start)
  body = text[start:end]
  locals_match = re.search(r"\.locals (\d+)", body)
  if not locals_match:
    raise ValueError("Unexpected method " + signature)
  body = body[:locals_match.start()] + ".locals 1\n\n" + new_body + "\n"
  return text[:start] + body + text[end:]


def patch_mock_location(navi_text, app_text):
  """HUD13.4: accept mock-provider fixes (nMirror '차량 GPS'). Naver tags mock
  locations and its map-matching filters them, which froze navigation while the
  phone sat in the console box; TMAP has no such check."""
  navi_text = replace_method_body(
    navi_text, ".method public static final a(Landroid/location/Location;)Z",
    "    const/4 v0, 0x0\n\n    return v0")
  app_text = replace_method_body(
    app_text, ".method public final b(Landroid/location/Location;)Ljava/lang/String;",
    "    invoke-virtual {p1}, Landroid/location/Location;->getProvider()Ljava/lang/String;\n\n"
    "    move-result-object v0\n\n    return-object v0")
  return navi_text, app_text


def patch_provider(text):
  start = text.index(".method public constructor <init>(Landroidx/car/app/CarContext;")
  end = text.index(".end method", start)
  body = text[start:end]
  if body.count("    return-void") != 1 or "CarrotCarMapSnapshot" in body:
    raise ValueError("Unexpected MapProvider constructor")
  return text[:start] + body.replace("    return-void", PROVIDER_HOOK) + text[end:]


def decode(java, apktool, work, name, manifest, dex):
  apk = work / (name + ".apk")
  with zipfile.ZipFile(apk, "w") as z:
    z.writestr("AndroidManifest.xml", manifest)
    z.writestr("classes.dex", dex)
  subprocess.run([str(java), "-jar", str(apktool), "d", "-r", "-o", str(work / name), str(apk),
                  "-p", str(work / "framework")], check=True)
  return work / name


def build(java, apktool, work, name):
  out = work / (name + "-patched.apk")
  subprocess.run([str(java), "-jar", str(apktool), "b", str(work / name), "-o", str(out),
                  "-p", str(work / "framework")], check=True)
  with zipfile.ZipFile(out) as z:
    return z.read("classes.dex")


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  for key in ("input", "java-home", "sdk", "apktool", "work", "output"):
    parser.add_argument("--" + key, type=Path, required=True)
  args = parser.parse_args()
  if hashlib.sha256(args.input.read_bytes()).hexdigest() != HUD11_SHA256:
    raise ValueError("Expected verified HUD11 APKS")
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
    provider_dex = original.read("classes5.dex")
    app_dex = original.read("classes.dex")

  stub = work / "src" / PACKAGE / "CarrotNaverBridge.java"
  stub.parent.mkdir(parents=True)
  stub.write_text("package com.naver.map.carrot; class CarrotNaverBridge { void sendBitmap(Object b) {} void clearMap() {} }",
                  encoding="utf-8")
  classes = work / "classes"
  classes.mkdir()
  subprocess.run([str(javac), "--release", "8", "-encoding", "UTF-8", "-cp", str(android), "-d", str(classes),
                  str(stub), str(here / "CarrotCarMapSnapshot.java"), str(here / "CarrotHudLog.java")], check=True)
  with zipfile.ZipFile(work / "new.jar", "w") as jar:
    for item in classes.rglob("*.class"):
      if item.name != "CarrotNaverBridge.class":
        jar.write(item, item.relative_to(classes).as_posix())
  (work / "dex").mkdir()
  subprocess.run([str(java), "-cp", str(args.sdk / "build-tools/35.0.0/lib/d8.jar"), "com.android.tools.r8.D8",
                  "--min-api", "26", "--lib", str(android), "--output", str(work / "dex"), str(work / "new.jar")], check=True)

  bridge = decode(java, args.apktool, work, "bridge", manifest, bridge_dex)
  new = decode(java, args.apktool, work, "new", manifest, (work / "dex/classes.dex").read_bytes())
  provider = decode(java, args.apktool, work, "provider", manifest, provider_dex)
  app = decode(java, args.apktool, work, "app", manifest, app_dex)
  for item in (new / "smali" / PACKAGE).glob("Carrot*.smali"):
    shutil.copyfile(item, bridge / "smali" / PACKAGE / item.name)
  bridge_smali = bridge / "smali" / PACKAGE / "CarrotNaverBridge.smali"
  bridge_smali.write_text(patch_set_activity(patch_bridge(bridge_smali.read_text(encoding="utf-8"))), encoding="utf-8")
  provider_smali = provider / "smali/com/naver/map/core/auto/map/MapProvider.smali"
  provider_smali.write_text(patch_provider(provider_smali.read_text(encoding="utf-8")), encoding="utf-8")
  navi_smali = provider / "smali/com/naver/maps/navi/mapmatching/LocationExtensionsKt.smali"
  app_smali = app / "smali/com/naver/map/core/common/location/LocationManager$Companion.smali"
  navi_text, app_text = patch_mock_location(navi_smali.read_text(encoding="utf-8"),
                                            app_smali.read_text(encoding="utf-8"))
  navi_smali.write_text(navi_text, encoding="utf-8")
  app_smali.write_text(app_text, encoding="utf-8")
  replacement = {"classes43.dex": build(java, args.apktool, work, "bridge"),
                 "classes5.dex": build(java, args.apktool, work, "provider"),
                 "classes.dex": build(java, args.apktool, work, "app")}

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
  print("Verified: only classes.dex, classes5.dex and classes43.dex changed. UNSIGNED:", args.output)


if __name__ == "__main__":
  main()
