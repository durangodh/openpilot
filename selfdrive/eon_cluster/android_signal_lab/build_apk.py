#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reproducible standalone Linux APK build. No repository or device mutations."""
from pathlib import Path
import concurrent.futures
import hashlib
import base64
import xml.etree.ElementTree as ET
import json
import os
import shutil
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent
CACHE = Path(os.environ.get('SIGNAL_LAB_BUILD_CACHE', str(ROOT / '.deps')))
BUILD = ROOT / 'build'
DEPS = json.loads((ROOT / 'dependencies.json').read_text())


def sha(path):
  return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
  subprocess.run([str(x) for x in args], check=True)


def fetch(name):
  item = DEPS[name]
  target = CACHE / name
  if not target.exists() or sha(target) != item['sha256']:
    temporary = target.with_suffix('.download')
    urllib.request.urlretrieve(item['url'], temporary)
    if sha(temporary) != item['sha256']:
      temporary.unlink()
      raise RuntimeError('Dependency checksum mismatch: ' + name)
    temporary.replace(target)
  return target


def main():
  java = shutil.which('java')
  keytool = shutil.which('keytool')
  if not java or not keytool:
    raise SystemExit('Install JDK 17+ and put java/keytool on PATH')
  CACHE.mkdir(parents=True, exist_ok=True)
  if BUILD.exists():
    shutil.rmtree(BUILD)
  (BUILD / 'classes').mkdir(parents=True)
  (BUILD / 'dex').mkdir()
  with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
    deps = dict(zip(DEPS, pool.map(fetch, DEPS)))
  with zipfile.ZipFile(deps['platform.zip']) as archive:
    jar = next(n for n in archive.namelist() if n.endswith('/android.jar'))
    (CACHE / 'android.jar').write_bytes(archive.read(jar))
  with zipfile.ZipFile(deps['aapt2.jar']) as archive:
    (CACHE / 'aapt2').write_bytes(archive.read('aapt2'))
  (CACHE / 'aapt2').chmod(0o755)
  for name, folder in [('litert.aar', 'litert'), ('litert-api.aar', 'litert-api')]:
    (CACHE / folder).mkdir(exist_ok=True)
    with zipfile.ZipFile(deps[name]) as archive:
      (CACHE / folder / 'classes.jar').write_bytes(archive.read('classes.jar'))
      if name == 'litert.aar':
        (CACHE / 'libtensorflowlite_jni.so').write_bytes(archive.read('jni/arm64-v8a/libtensorflowlite_jni.so'))
  model = ROOT / 'assets/korean-signal.tflite'
  if not model.exists():
    model.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve('https://github.com/flywithu/LightSignal/releases/download/v20260727/model.tflite', model)
  if sha(model) != '458c8f1273ae407e6fa8334159a7f6dc29c88a2680b15eae2771105b0bdeffc2':
    raise SystemExit('Pinned Korean model checksum mismatch')
  jars = [CACHE / 'android.jar', CACHE / 'litert/classes.jar', CACHE / 'litert-api/classes.jar']
  sources = sorted((ROOT / 'src').rglob('*.java'))
  run([java, '-m', 'jdk.compiler/com.sun.tools.javac.Main', '-source', '11', '-target', '11', '-encoding', 'UTF-8',
       '-cp', os.pathsep.join(map(str, jars)), '-d', BUILD / 'classes'] + sources)
  run([java, '-cp', CACHE / 'r8.jar', 'com.android.tools.r8.D8', '--lib', CACHE / 'android.jar', '--min-api', '30',
       '--output', BUILD / 'dex'] + sorted((BUILD / 'classes').rglob('*.class')) + jars[1:])
  unsigned = BUILD / 'unsigned.apk'
  manifest = ET.parse(ROOT / 'AndroidManifest.xml')
  manifest.getroot().set('{http://schemas.android.com/apk/res/android}versionCode', os.environ.get('SIGNAL_LAB_VERSION_CODE', '3'))
  manifest.getroot().set('{http://schemas.android.com/apk/res/android}versionName', os.environ.get('SIGNAL_LAB_VERSION_NAME', '0.3-korea-stable'))
  manifest.write(BUILD / 'AndroidManifest.xml', encoding='utf-8', xml_declaration=True)
  run([CACHE / 'aapt2', 'link', '-I', CACHE / 'android.jar', '--manifest', BUILD / 'AndroidManifest.xml',
       '-A', ROOT / 'assets', '-o', unsigned])
  with zipfile.ZipFile(unsigned, 'a', compression=zipfile.ZIP_DEFLATED) as archive:
    for dex in (BUILD / 'dex').glob('*.dex'):
      archive.write(dex, dex.name)
    archive.write(CACHE / 'libtensorflowlite_jni.so', 'lib/arm64-v8a/libtensorflowlite_jni.so')
  key = ROOT / 'signing/trial.p12'
  key.parent.mkdir(exist_ok=True)
  encoded_key = ROOT / 'signing/trial.p12.base64'
  if not key.exists() and encoded_key.exists():
    key.write_bytes(base64.b64decode(encoded_key.read_text(), validate=False))
    key.chmod(0o600)
  if not key.exists():
    run([keytool, '-genkeypair', '-storetype', 'PKCS12', '-keystore', key, '-storepass', 'android', '-keypass', 'android',
         '-alias', 'signallab', '-keyalg', 'RSA', '-keysize', '2048', '-validity', '10000', '-dname', 'CN=EON Signal Lab Trial'])
  apk = ROOT / 'EonSignalLab-Korea-latest.apk'
  run([java, '--class-path', CACHE / 'apksig.jar', ROOT / 'Sign.java', key, unsigned, apk])
  apk.with_suffix('.apk.sha256').write_text(sha(apk) + '  ' + apk.name + '\n')
  print('Built and verified:', apk)


if __name__ == '__main__':
  main()
