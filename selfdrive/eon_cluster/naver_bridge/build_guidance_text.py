"""Build HUD13.11: send Naver's on-screen TBT text as the HUD guidance title.

Input is the published HUD13.10 APKS. Only classes43.dex (CarrotNaverBridge)
changes. guidance() used TbtDataItem.v() (roadName) for both main_text and
road_name. Naver builds expressway rest-area items with roadName=null, so the
HUD fell back to the current road name ("서해안고속도로") while Naver itself
showed "군산휴게소". Naver's banner text is TbtDataItem.n() (facility name ->
next road name -> direction -> guide string). main_text now uses n() and falls
back to v() when n() is blank; road_name stays v().
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import zipfile

from build_patch import signature_entry


HUD13_10_SHA256 = "4b1a91d85940e2ec16db951c6a745b9ecd979289ed8aa502d3eb029345b214b0"
CALL = "Lcom/naver/map/carrot/CarrotNaverBridge;->call(Ljava/lang/Object;Ljava/lang/String;)Ljava/lang/Object;"
STRING = "Lcom/naver/map/carrot/CarrotNaverBridge;->string(Ljava/lang/Object;)Ljava/lang/String;"
ESC = "Lcom/naver/map/carrot/CarrotNaverBridge;->esc(Ljava/lang/String;)Ljava/lang/String;"


def replace_once(text, old, new, label):
  if text.count(old) != 1:
    raise ValueError("Unexpected %s match count: %d" % (label, text.count(old)))
  return text.replace(old, new)


def patch_guidance(text):
  start = text.index(".method private guidance(Ljava/lang/Object;)Ljava/lang/String;")
  end = text.index(".end method", start)
  body = text[start:end]
  # v7 holds the display text; one extra local register.
  body = replace_once(body, "    .locals 7\n", "    .locals 8\n", "guidance locals")
  old_road = ("    const-string v3, \"v\"\n\n"
              "    invoke-static {v0, v3}, " + CALL + "\n\n"
              "    move-result-object v0\n\n"
              "    invoke-static {v0}, " + STRING + "\n\n"
              "    move-result-object v0\n")
  new_road = ("    const-string v3, \"n\"\n\n"
              "    invoke-static {v0, v3}, " + CALL + "\n\n"
              "    move-result-object v7\n\n"
              "    invoke-static {v7}, " + STRING + "\n\n"
              "    move-result-object v7\n\n"
              + old_road +
              "\n"
              "    invoke-virtual {v7}, Ljava/lang/String;->trim()Ljava/lang/String;\n\n"
              "    move-result-object v7\n\n"
              "    invoke-virtual {v7}, Ljava/lang/String;->isEmpty()Z\n\n"
              "    move-result v3\n\n"
              "    if-eqz v3, :carrot_guidance_text_ok\n\n"
              "    move-object v7, v0\n\n"
              "    :carrot_guidance_text_ok\n")
  body = replace_once(body, old_road, new_road, "roadName lookup")
  old_main = ("    const-string v1, \",\\\"main_text\\\":\\\"\"\n\n"
              "    invoke-virtual {p1, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;\n\n"
              "    move-result-object p1\n\n"
              "    invoke-static {v0}, " + ESC + "\n")
  new_main = old_main.replace("    invoke-static {v0}, " + ESC, "    invoke-static {v7}, " + ESC)
  body = replace_once(body, old_main, new_main, "main_text value")
  return text[:start] + body + text[end:]


def run(*args):
  subprocess.run([str(arg) for arg in args], check=True)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  for key in ("input", "java-home", "apktool", "work", "output"):
    parser.add_argument("--" + key, type=Path, required=True)
  args = parser.parse_args()
  if hashlib.sha256(args.input.read_bytes()).hexdigest() != HUD13_10_SHA256:
    raise ValueError("Expected verified HUD13.10 APKS")
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
    bridge_dex = original.read("classes43.dex")

  # Apktool only treats classes.dex as the primary dex in a minimal container.
  mini = work / "mini.apk"
  with zipfile.ZipFile(mini, "w") as z:
    z.writestr("AndroidManifest.xml", manifest)
    z.writestr("classes.dex", bridge_dex)
  decoded = work / "decoded"
  run(java, "-jar", args.apktool, "d", "-r", "-o", decoded, mini)

  bridge = decoded / "smali/com/naver/map/carrot/CarrotNaverBridge.smali"
  bridge.write_text(patch_guidance(bridge.read_text(encoding="utf-8")), encoding="utf-8")

  rebuilt = work / "rebuilt.apk"
  run(java, "-jar", args.apktool, "b", decoded, "-o", rebuilt)
  with zipfile.ZipFile(rebuilt) as patched:
    bridge_replacement = patched.read("classes.dex")

  args.output.parent.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(base_apk) as original, zipfile.ZipFile(args.output, "w") as output:
    for entry in original.infolist():
      if signature_entry(entry.filename):
        continue
      info = zipfile.ZipInfo(entry.filename, date_time=entry.date_time)
      info.compress_type = entry.compress_type
      info.external_attr = entry.external_attr
      replacement = bridge_replacement if entry.filename == "classes43.dex" else None
      output.writestr(info, replacement if replacement is not None else original.read(entry))

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
