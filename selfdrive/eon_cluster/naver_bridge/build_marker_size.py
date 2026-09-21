"""HUD13.8: refresh navigation marker pixel dimensions after display density changes.

Input is the verified HUD13.7 release; only classes6.dex/classes12.dex change.
Output is unsigned and must use the existing HUD release certificate.
"""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

from build_car_snapshot import decode, build
from build_patch import signature_entry

HUD137_SHA256 = "70664f27927f95a78918666669639f400b585b7e6c2eca47bc880e6807a80681"
PACKAGE = Path("com/naver/map/carrot")
MANAGER = "com/naver/map/core/navigation/NaviCarvatarIconManager"
LOADER = MANAGER + "$loadIcons$2$results$1$1"
WRAPPER = "com/naver/maps/navi/ui/map/map/NaverMapWrapper"
HELPER = "Lcom/naver/map/carrot/CarrotMarkerSize;"


def once(text, old, new):
  if text.count(old) != 1:
    raise ValueError("Unexpected input: " + old[:100])
  return text.replace(old, new)


def patch_manager(text):
  text = once(text, ".field public final c:I", ".field public final c:I\n\n.field public final carrotSourceDensity:F")
  pattern = (r"(invoke-static \{v0\}, Lcom/naver/map/base/common/android/DisplayUtilsKt;"
             r"->g\(Landroid/content/Context;\)F\s+(?:\.line \d+\s+)*move-result v0)")
  text, count = re.subn(pattern, r"\1\n\n    iput v0, p0, L" + MANAGER + ";->carrotSourceDensity:F", text)
  if count != 1:
    raise ValueError("Unexpected marker density initialization")
  return text


def patch_loader(text):
  # v1 is dead at the successful/failed load convergence; the result in p1 is unchanged.
  hook = ("    iget-object v1, p0, L" + LOADER + ";->a0:L" + MANAGER + ";\n"
          "    iget v1, v1, L" + MANAGER + ";->carrotSourceDensity:F\n"
          "    invoke-static {p1, v1}, " + HELPER + "->remember(Ljava/lang/Object;F)V\n\n")
  target = "    invoke-static {p1}, Lkotlin/Result;->a(Ljava/lang/Object;)Lkotlin/Result;"
  return once(text, target, hook + target)


def patch_wrapper(text):
  if "carrotRefreshMarkerSize" in text:
    raise ValueError("Marker size already patched")
  for signature in [".method public I(Lcom/naver/maps/geometry/LatLng;D)V",
                    ".method public t(Lcom/naver/maps/map/overlay/OverlayImage;)V",
                    ".method public z0()V"]:
    start = text.index(signature)
    end = text.index(".end method", start)
    method = text[start:end]
    index = method.rindex("    return-void")
    method = (method[:index] + "    invoke-direct {p0}, L" + WRAPPER
              + ";->carrotRefreshMarkerSize()V\n\n" + method[index:])
    text = text[:start] + method + text[end:]
  return text + """
.method private carrotRefreshMarkerSize()V
    .locals 2
    iget-boolean v0, p0, Lcom/naver/maps/navi/ui/map/map/NaverMapWrapper;->b0:Z
    if-nez v0, :carrot_done
    iget-object v0, p0, Lcom/naver/maps/navi/ui/map/map/NaverMapWrapper;->c0:Lcom/naver/maps/map/overlay/LocationOverlay;
    invoke-virtual {p0}, Lcom/naver/maps/navi/ui/map/map/NaverMapWrapper;->getContext()Landroid/content/Context;
    move-result-object v1
    invoke-static {v0, v1}, Lcom/naver/map/carrot/CarrotMarkerSize;->apply(Lcom/naver/maps/map/overlay/LocationOverlay;Landroid/content/Context;)V
    :carrot_done
    return-void
.end method
"""


STUBS = {
  "com/naver/maps/map/overlay/OverlayImage.java": """package com.naver.maps.map.overlay;
public abstract class OverlayImage {
  public abstract int j(android.content.Context c);
  public abstract int i(android.content.Context c);
}""",
  "com/naver/maps/map/overlay/LocationOverlay.java": """package com.naver.maps.map.overlay;
public class LocationOverlay {
  public OverlayImage getIcon() { throw new UnsupportedOperationException(); }
  public int getIconWidth() { throw new UnsupportedOperationException(); }
  public int getIconHeight() { throw new UnsupportedOperationException(); }
  public void setIconWidth(int w) { throw new UnsupportedOperationException(); }
  public void setIconHeight(int h) { throw new UnsupportedOperationException(); }
}""",
}


def compile_java(java_home, sources, classes, classpath=None, ecj=None):
  suffix = ".exe" if (java_home / "bin/java.exe").exists() else ""
  if ecj:
    command = [str(java_home / ("bin/java" + suffix)), "-jar", str(ecj), "-8"]
  else:
    command = [str(java_home / ("bin/javac" + suffix)), "--release", "8"]
  command += ["-encoding", "UTF-8", "-d", str(classes)]
  if classpath:
    command += ["-cp", str(classpath)]
  subprocess.run(command + [str(p) for p in sources], check=True)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  for key in ("input", "java-home", "sdk", "apktool", "work", "output"):
    parser.add_argument("--" + key, type=Path, required=True)
  parser.add_argument("--ecj", type=Path, help="Optional Java compiler for JRE-only development hosts")
  args = parser.parse_args()
  if hashlib.sha256(args.input.read_bytes()).hexdigest() != HUD137_SHA256:
    raise ValueError("Expected verified HUD13.7 APKS")
  if args.output.exists():
    raise FileExistsError(args.output)
  work = args.work.resolve()
  work.mkdir(parents=True, exist_ok=False)
  java = args.java_home / ("bin/java.exe" if (args.java_home / "bin/java.exe").exists() else "bin/java")
  android = args.sdk / "platforms/android-35/android.jar"
  with zipfile.ZipFile(args.input) as bundle:
    (work / "base.apk").write_bytes(bundle.read("base.apk"))
  with zipfile.ZipFile(work / "base.apk") as original:
    manifest = original.read("AndroidManifest.xml")
    originals = {n: original.read(n) for n in ("classes6.dex", "classes12.dex")}

  sources = []
  for name, content in STUBS.items():
    p = work / "src" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    sources.append(p)
  classes = work / "classes"
  classes.mkdir()
  compile_java(args.java_home, sources + [Path(__file__).with_name("CarrotMarkerSize.java")],
               classes, android, args.ecj)
  with zipfile.ZipFile(work / "marker.jar", "w") as jar:
    for p in (classes / PACKAGE).glob("CarrotMarkerSize*.class"):
      jar.write(p, p.relative_to(classes).as_posix())
  (work / "dex").mkdir()
  subprocess.run([str(java), "-cp", str(args.sdk / "build-tools/35.0.0/lib/d8.jar"), "com.android.tools.r8.D8",
                  "--min-api", "26", "--lib", str(android), "--output", str(work / "dex"), str(work / "marker.jar")], check=True)
  wrapper = decode(java, args.apktool, work, "wrapper", manifest, originals["classes6.dex"])
  manager = decode(java, args.apktool, work, "manager", manifest, originals["classes12.dex"])
  helper = decode(java, args.apktool, work, "helper", manifest, (work / "dex/classes.dex").read_bytes())
  for root, name, patch in [(wrapper, WRAPPER, patch_wrapper), (manager, MANAGER, patch_manager),
                            (manager, LOADER, patch_loader)]:
    p = root / "smali" / (name + ".smali")
    p.write_text(patch(p.read_text(encoding="utf-8")), encoding="utf-8")
  target = wrapper / "smali" / PACKAGE
  target.mkdir(parents=True, exist_ok=True)
  for p in (helper / "smali" / PACKAGE).glob("CarrotMarkerSize*.smali"):
    shutil.copyfile(p, target / p.name)
  replacement = {"classes6.dex": build(java, args.apktool, work, "wrapper"),
                 "classes12.dex": build(java, args.apktool, work, "manager")}
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
    expected = {n for n in original.namelist() if not signature_entry(n)}
    if set(output.namelist()) != expected:
      raise ValueError("Unexpected output entries")
    for name in expected - replacement.keys():
      if original.read(name) != output.read(name):
        raise ValueError("Unexpected payload change: " + name)
    if output.testzip() is not None:
      raise ValueError("Invalid output ZIP")
  print("Verified: only classes6.dex and classes12.dex changed. UNSIGNED:", args.output)


if __name__ == "__main__":
  main()
