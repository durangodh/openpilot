"""Build HUD13.9 with a faster, shorter-stall Naver HUD map snapshot loop.

Input is the published HUD13.7 APKS. Only classes43.dex in the base APK is
changed: the bridge map tick moves from 500 ms to 200 ms and a missing SDK
snapshot is retried after 1.2 s instead of 3 s.
"""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import zipfile

from build_patch import signature_entry


HUD13_7_SHA256 = "70664f27927f95a78918666669639f400b585b7e6c2eca47bc880e6807a80681"


def replace_once(text, old, new, label):
  if text.count(old) != 1:
    raise ValueError("Unexpected %s match count: %d" % (label, text.count(old)))
  return text.replace(old, new)


def patch_bridge(text):
  start = text.index(".method public run()V")
  end = text.index(".end method", start)
  body = text[start:end]
  body = replace_once(body, "    const-wide/16 v4, 0x1f4\n\n    add-long/2addr v2, v4",
                      "    const-wide/16 v4, 0xc8\n\n    add-long/2addr v2, v4",
                      "500 ms map interval")
  return text[:start] + body + text[end:]


def patch_snapshot(text):
  text = replace_once(text,
                      ".field private static final SNAPSHOT_TIMEOUT_MS:J = 0xbb8L",
                      ".field private static final SNAPSHOT_TIMEOUT_MS:J = 0x4b0L",
                      "snapshot timeout field")
  start = text.index(".method public static capture(")
  end = text.index(".end method", start)
  body = text[start:end]
  body = replace_once(body, "    const-wide/16 v10, 0xbb8",
                      "    const-wide/16 v10, 0x4b0", "snapshot timeout code")
  return text[:start] + body + text[end:]


def run(*args):
  subprocess.run([str(arg) for arg in args], check=True)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  for key in ("input", "java-home", "apktool", "work", "output"):
    parser.add_argument("--" + key, type=Path, required=True)
  args = parser.parse_args()
  if hashlib.sha256(args.input.read_bytes()).hexdigest() != HUD13_7_SHA256:
    raise ValueError("Expected verified HUD13.7 APKS")
  if args.output.exists():
    raise FileExistsError(args.output)

  work = args.work.resolve()
  work.mkdir(parents=True, exist_ok=False)
  java = args.java_home / ("bin/java.exe" if (args.java_home / "bin/java.exe").exists() else "bin/java")

  with zipfile.ZipFile(args.input) as bundle:
    base = bundle.read("base.apk")
  base_apk = work / "base.apk"
  base_apk.write_bytes(base)
  with zipfile.ZipFile(base_apk) as original:
    manifest = original.read("AndroidManifest.xml")
    dex = original.read("classes43.dex")

  # Apktool only treats classes.dex as the primary dex in a minimal container.
  mini = work / "mini.apk"
  with zipfile.ZipFile(mini, "w") as z:
    z.writestr("AndroidManifest.xml", manifest)
    z.writestr("classes.dex", dex)
  decoded = work / "decoded"
  run(java, "-jar", args.apktool, "d", "-r", "-o", decoded, mini)

  package = decoded / "smali/com/naver/map/carrot"
  bridge = package / "CarrotNaverBridge.smali"
  snapshot = package / "CarrotCarMapSnapshot.smali"
  bridge.write_text(patch_bridge(bridge.read_text(encoding="utf-8")), encoding="utf-8")
  snapshot.write_text(patch_snapshot(snapshot.read_text(encoding="utf-8")), encoding="utf-8")

  rebuilt = work / "rebuilt.apk"
  run(java, "-jar", args.apktool, "b", decoded, "-o", rebuilt)
  with zipfile.ZipFile(rebuilt) as patched:
    replacement = patched.read("classes.dex")

  args.output.parent.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(base_apk) as original, zipfile.ZipFile(args.output, "w") as output:
    for entry in original.infolist():
      if signature_entry(entry.filename):
        continue
      info = zipfile.ZipInfo(entry.filename, date_time=entry.date_time)
      info.compress_type = entry.compress_type
      info.external_attr = entry.external_attr
      output.writestr(info, replacement if entry.filename == "classes43.dex" else original.read(entry))

  with zipfile.ZipFile(base_apk) as original, zipfile.ZipFile(args.output) as output:
    changed = []
    for entry in original.infolist():
      if signature_entry(entry.filename):
        continue
      if original.read(entry) != output.read(entry.filename):
        changed.append(entry.filename)
    if changed != ["classes43.dex"]:
      raise RuntimeError("Unexpected changed entries: " + repr(changed))
    if output.testzip() is not None:
      raise RuntimeError("Corrupt output APK")
  print("Verified: only classes43.dex changed. UNSIGNED:", args.output)


if __name__ == "__main__":
  main()
