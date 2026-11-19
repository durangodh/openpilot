#!/usr/bin/env python3
"""Build the standalone app with Android SDK tools and pinned native engine.

No Gradle/plugin dependencies, no upstream APK modification. New manifest/dex.
Signing is explicit; CI uses the repository's existing Android signing key.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
UPSTREAM_URL = 'https://github.com/bin9208/jetlink/releases/download/v0.9.0-carrot.46/jetlink-carrot-0.9.0-carrot.46.apk'
UPSTREAM_SHA = '08a083207f58ce574f4ab0bfab13ec81123b7ab782bd24e7f21251e2eb73fd89'


def run(*args):
  subprocess.run([str(a) for a in args], check=True)


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--sdk', default=os.environ.get('ANDROID_HOME'))
  parser.add_argument('--upstream', type=Path, default=ROOT / 'build/jetlink-carrot.46.apk')
  parser.add_argument('--output', type=Path, default=ROOT / 'build/EON-Link-0.1.0.apk')
  parser.add_argument('--keystore', type=Path, required=True)
  parser.add_argument('--alias', default='androiddebugkey')
  parser.add_argument('--store-pass-env', default='EONLINK_STORE_PASSWORD')
  parser.add_argument('--key-pass-env', default='EONLINK_KEY_PASSWORD')
  args = parser.parse_args()
  if not args.sdk:
    parser.error('--sdk or ANDROID_HOME is required')
  sdk = Path(args.sdk)
  tools = sdk / 'build-tools/35.0.0'
  android = sdk / 'platforms/android-35/android.jar'
  build = ROOT / 'build/package'
  if build.exists():
    shutil.rmtree(build)
  build.mkdir(parents=True)
  if not args.upstream.exists():
    args.upstream.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(UPSTREAM_URL, args.upstream)
  if hashlib.sha256(args.upstream.read_bytes()).hexdigest() != UPSTREAM_SHA:
    raise SystemExit('Pinned Jetlink engine APK SHA-256 mismatch')
  run(tools / 'aapt2', 'compile', '--dir', ROOT / 'res', '-o', build / 'resources.zip')
  manifest = build / 'AndroidManifest.xml'
  code = int(os.environ.get('EONLINK_VERSION_CODE', '1'))
  if not 1 <= code <= 2100000000:
    raise SystemExit('Invalid Android versionCode')
  manifest.write_text((ROOT / 'AndroidManifest.xml').read_text().replace('android:versionCode="1"', 'android:versionCode="%d"' % code))
  run(tools / 'aapt2', 'link', '-I', android, '--manifest', manifest,
      '--java', build / 'generated', '-o', build / 'base.apk', build / 'resources.zip')
  sources = sorted((ROOT / 'src').rglob('*.java')) + sorted((build / 'generated').rglob('*.java'))
  javac = Path(os.environ['JAVA_HOME']) / 'bin/javac' if os.environ.get('JAVA_HOME') else 'javac'
  run(javac, '-encoding', 'UTF-8', '-source', '8', '-target', '8', '-classpath', android, '-d', build / 'classes', *sources)
  classes = sorted((build / 'classes').rglob('*.class'))
  dex = build / 'dex'; dex.mkdir()
  run(tools / 'd8', '--release', '--min-api', '31', '--lib', android, '--output', dex, *classes)
  unsigned = build / 'unsigned.apk'; shutil.copyfile(build / 'base.apk', unsigned)
  with zipfile.ZipFile(unsigned, 'a', compression=zipfile.ZIP_DEFLATED) as out, zipfile.ZipFile(args.upstream) as original:
    out.write(dex / 'classes.dex', 'classes.dex')
    # Only the Snapdragon ORT engine; no Kotlin/Compose/USB/HUD/update code.
    for entry in original.namelist():
      name = entry.rsplit('/', 1)[-1]
      if entry.startswith('lib/arm64-v8a/') and (name.startswith('libQnnHtp') or name in
          ('libjetlink.so', 'libonnxruntime.so', 'libc++_shared.so', 'libQnnSystem.so')):
        out.writestr(entry, original.read(entry))
    out.write(ROOT / 'NOTICE.txt', 'assets/NOTICE.txt')
    out.write(ROOT / 'JETLINK-LICENSE', 'assets/JETLINK-LICENSE')
  aligned = build / 'aligned.apk'
  run(tools / 'zipalign', '-f', '-P', '16', '4', unsigned, aligned)
  args.output.parent.mkdir(parents=True, exist_ok=True)
  run(tools / 'apksigner', 'sign', '--ks', args.keystore, '--ks-key-alias', args.alias,
      '--ks-pass', 'env:' + args.store_pass_env, '--key-pass', 'env:' + args.key_pass_env,
      '--out', args.output, aligned)
  run(tools / 'apksigner', 'verify', '--verbose', '--print-certs', args.output)
  args.output.with_suffix('.apk.sha256').write_text(hashlib.sha256(args.output.read_bytes()).hexdigest() + '  ' + args.output.name + '\n')
  print(args.output)


if __name__ == '__main__':
  main()
