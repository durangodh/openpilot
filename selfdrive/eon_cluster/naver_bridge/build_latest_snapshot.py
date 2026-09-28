"""HUD13.14: replace only snapshot helper classes in verified HUD13.13.

Preserves the released SDK render-wake patch, 200 ms bridge cadence, guidance,
voice-button and marker fixes. Fails closed if those capture hooks are absent.
"""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import zipfile

from build_car_snapshot import decode, build, PACKAGE
from build_patch import signature_entry

BASE_SHA256 = "1d964b6d542c1aa24b58c032c563db57ad8bca104f295e03d84bb76a23b394ae"


def verify_hooks(bridge, sdk):
    start = bridge.index('.method public run()V')
    body = bridge[start:bridge.index('.end method', start)]
    if 'const-wide/16 v4, 0xc8' not in body:
        raise ValueError('Missing released 200 ms map cadence')
    if 'CarrotCarMapSnapshot;->capture(' not in bridge:
        raise ValueError('Missing snapshot hook')
    start = sdk.index('.method public p2(ZLcom/naver/maps/map/NaverMap$SnapshotReadyCallback;)V')
    body = sdk[start:sdk.index('.end method', start)]
    if 'MapRendererScheduler;->requestRender()V' not in body:
        raise ValueError('Missing released SDK render wake')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('input', 'java-home', 'sdk', 'apktool', 'work', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    if hashlib.sha256(args.input.read_bytes()).hexdigest() != BASE_SHA256:
        raise ValueError('Expected verified HUD13.13 APKS')
    if args.output.exists():
        raise FileExistsError(args.output)
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    suffix = '.exe' if (args.java_home / 'bin/java.exe').exists() else ''
    java, javac = [args.java_home / ('bin/' + n + suffix) for n in ('java', 'javac')]
    android = args.sdk / 'platforms/android-35/android.jar'
    here = Path(__file__).resolve().parent
    with zipfile.ZipFile(args.input) as bundle:
        (work / 'base.apk').write_bytes(bundle.read('base.apk'))
    with zipfile.ZipFile(work / 'base.apk') as original:
        manifest = original.read('AndroidManifest.xml')
        bridge_dex, sdk_dex = [original.read(n) for n in ('classes43.dex', 'classes.dex')]

    stub = work / 'src' / PACKAGE / 'CarrotNaverBridge.java'
    stub.parent.mkdir(parents=True)
    stub.write_text('package com.naver.map.carrot; class CarrotNaverBridge {}', encoding='utf-8')
    classes = work / 'classes'
    classes.mkdir()
    subprocess.run([str(javac), '--release', '8', '-encoding', 'UTF-8', '-cp', str(android),
                    '-d', str(classes), str(stub), str(here / 'CarrotCarMapSnapshot.java'),
                    str(here / 'CarrotTrafficSignalCapture.java'), str(here / 'CarrotHudLog.java')], check=True)
    with zipfile.ZipFile(work / 'snapshot.jar', 'w') as jar:
        for item in (classes / PACKAGE).glob('CarrotCarMapSnapshot*.class'):
            jar.write(item, item.relative_to(classes).as_posix())
    (work / 'dex').mkdir()
    subprocess.run([str(java), '-cp', str(args.sdk / 'build-tools/35.0.0/lib/d8.jar'),
                    'com.android.tools.r8.D8', '--min-api', '26', '--lib', str(android),
                    '--output', str(work / 'dex'), str(work / 'snapshot.jar')], check=True)
    bridge = decode(java, args.apktool, work, 'bridge', manifest, bridge_dex)
    sdk = decode(java, args.apktool, work, 'sdk', manifest, sdk_dex)
    new = decode(java, args.apktool, work, 'new', manifest, (work / 'dex/classes.dex').read_bytes())
    target = bridge / 'smali' / PACKAGE
    bridge_file = target / 'CarrotNaverBridge.smali'
    verify_hooks(bridge_file.read_text(encoding='utf-8'),
                 (sdk / 'smali/com/naver/maps/map/NaverMap.smali').read_text(encoding='utf-8'))
    # These are generated files inside this fresh build directory, not repo files.
    for item in target.glob('CarrotCarMapSnapshot*.smali'):
        item.unlink()
    for item in (new / 'smali' / PACKAGE).glob('CarrotCarMapSnapshot*.smali'):
        shutil.copyfile(item, target / item.name)
    replacement = build(java, args.apktool, work, 'bridge')
    verified = decode(java, args.apktool, work, 'verify', manifest, replacement)
    snapshot = (verified / 'smali' / PACKAGE / 'CarrotCarMapSnapshot.smali').read_text(encoding='utf-8')
    for token in ('drainLatestFrames()V', 'pendingFrame:', 'activeRequest:', 'generation:'):
        if token not in snapshot:
            raise ValueError('Missing assembled snapshot guard: ' + token)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(work / 'base.apk') as original, zipfile.ZipFile(args.output, 'w') as output:
        for entry in original.infolist():
            if not signature_entry(entry.filename):
                output.writestr(entry, replacement if entry.filename == 'classes43.dex' else original.read(entry))
    with zipfile.ZipFile(work / 'base.apk') as original, zipfile.ZipFile(args.output) as output:
        expected = {n for n in original.namelist() if not signature_entry(n)}
        if set(output.namelist()) != expected or output.testzip() is not None:
            raise ValueError('Invalid output archive')
        for name in expected - {'classes43.dex'}:
            if original.read(name) != output.read(name):
                raise ValueError('Unexpected payload change: ' + name)
    print('Verified: only classes43.dex changed; SDK/voice/marker payloads preserved')


if __name__ == '__main__':
    main()
