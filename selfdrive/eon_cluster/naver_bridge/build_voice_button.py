"""Build HUD13.12 from the exact HUD13.11 APKS; preserve all other APK entries.

The Compose MapButton receives Function0 v5 in NaviClovaButtonComponent.Q().
That callback is clova/o.invoke -> K -> R -> g0 (normal button behavior).
Expose that exact callback through a native accessibility-clickable ComposeView.
nMirror searches the existing btn_speech_recognition ID; no key forwarding is assumed.
Outputs an unsigned APK: release workflow must sign with the existing HUD key.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import zipfile

from build_patch import signature_entry

INPUT_SHA256 = "a584f0d22f00570e3a170cd2fd9bd39cf60582febb0be35f346c633d9fbbe880"
HELPER = "Lcom/naver/map/carrot/CarrotVoiceButton;"
COMPONENT = "com/naver/map/feature/navigation/renewal/clova/NaviClovaButtonComponent.smali"


def patch_component(text):
  start = text.index(".method public static final Q(")
  end = text.index(".end method", start)
  body = text[start:end]
  anchor = "    check-cast v5, Lkotlin/jvm/functions/Function0;"
  if body.count(anchor) != 1 or "CarrotVoiceButton" in text:
    raise ValueError("Unexpected Compose callback layout")
  if "invoke-direct {v6, v1, v0}, Lcom/naver/map/feature/navigation/renewal/clova/o;-><init>" not in body:
    raise ValueError("Unexpected microphone callback")
  # v1 is the component; v6 is dead after its callback moves to v5.
  hook = anchor + """

    invoke-virtual {v1}, Lcom/naver/map/core/common/ui/compose/ComposeComponent;->x()Lcom/naver/map/base/common/compose/ComposeView;
    move-result-object v6
    invoke-static {v6, v5}, Lcom/naver/map/carrot/CarrotVoiceButton;->bind(Landroid/view/View;Lkotlin/jvm/functions/Function0;)V
"""
  return text[:start] + body.replace(anchor, hook) + text[end:]


def run(*command):
  subprocess.run([str(c) for c in command], check=True)


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  for name in ("input", "apktool", "android-jar", "d8", "work", "output"):
    parser.add_argument("--" + name, type=Path, required=True)
  parser.add_argument("--java", default="java")
  parser.add_argument("--javac", default="javac")
  parser.add_argument("--ecj", type=Path)
  args = parser.parse_args()
  if hashlib.sha256(args.input.read_bytes()).hexdigest() != INPUT_SHA256:
    raise ValueError("Expected exact HUD13.11 APKS")
  if args.output.exists():
    raise FileExistsError(args.output)
  work = args.work.resolve()
  work.mkdir(parents=True, exist_ok=False)
  with zipfile.ZipFile(args.input) as bundle:
    base = bundle.read("base.apk")
  base_path = work / "base.apk"
  base_path.write_bytes(base)
  with zipfile.ZipFile(base_path) as original:
    manifest = original.read("AndroidManifest.xml")
    with zipfile.ZipFile(work / "mini.apk", "w") as z:
      z.writestr("AndroidManifest.xml", manifest)
      z.writestr("classes.dex", original.read("classes4.dex"))
  decoded = work / "decoded"
  run(args.java, "-jar", args.apktool, "d", "-r", "-o", decoded, work / "mini.apk")
  component_path = decoded / "smali" / COMPONENT
  component_path.write_text(patch_component(component_path.read_text(encoding="utf-8")), encoding="utf-8")
  stub = work / "stubs/kotlin/jvm/functions/Function0.java"
  stub.parent.mkdir(parents=True)
  stub.write_text("package kotlin.jvm.functions; public interface Function0<R> { R invoke(); }\n")
  classes = work / "classes"
  classes.mkdir()
  compiler = [args.java, "-jar", args.ecj, "-8"] if args.ecj else [args.javac, "--release", "8"]
  run(*compiler, "-encoding", "UTF-8", "-cp", args.android_jar, "-d", classes,
      stub, Path(__file__).with_name("CarrotVoiceButton.java"))
  jar = work / "helper.jar"
  with zipfile.ZipFile(jar, "w") as z:
    for item in (classes / "com").rglob("*.class"):
      z.write(item, item.relative_to(classes).as_posix())
  dex = work / "dex"
  dex.mkdir()
  run(args.java, "-cp", args.d8, "com.android.tools.r8.D8", "--min-api", "23", "--lib", args.android_jar,
      "--output", dex, jar)
  with zipfile.ZipFile(work / "helper.apk", "w") as z:
    z.writestr("AndroidManifest.xml", manifest)
    z.write(dex / "classes.dex", "classes.dex")
  helper = work / "helper"
  run(args.java, "-jar", args.apktool, "d", "-r", "-o", helper, work / "helper.apk")
  for item in (helper / "smali").rglob("*.smali"):
    target = decoded / "smali" / item.relative_to(helper / "smali")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(item, target)
  rebuilt = work / "rebuilt.apk"
  run(args.java, "-jar", args.apktool, "b", decoded, "-o", rebuilt)
  with zipfile.ZipFile(rebuilt) as z:
    replacements = {"classes4.dex": z.read("classes.dex")}
  args.output.parent.mkdir(parents=True, exist_ok=True)
  with zipfile.ZipFile(base_path) as original, zipfile.ZipFile(args.output, "w") as output:
    for entry in original.infolist():
      if not signature_entry(entry.filename):
        output.writestr(entry, replacements.get(entry.filename, original.read(entry)))
  with zipfile.ZipFile(base_path) as original, zipfile.ZipFile(args.output) as output:
    changed = {e.filename for e in original.infolist() if not signature_entry(e.filename)
               and original.read(e) != output.read(e.filename)}
    if changed != set(replacements) or output.testzip() is not None:
      raise RuntimeError("Unexpected APK changes: " + repr(changed))
  print("Verified: only classes4.dex changed; output is UNSIGNED", args.output)


if __name__ == "__main__":
  main()
