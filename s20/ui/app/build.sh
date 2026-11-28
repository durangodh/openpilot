#!/bin/bash
# Build s20ui.apk without Gradle. Needs a JDK and Android build-tools 35 + platform 35:
#   ANDROID_JAR=.../android-35/android.jar BT=.../build-tools(dir with aapt2, d8, zipalign, apksigner) bash s20/ui/app/build.sh
set -e
cd "$(dirname "$0")"
: "${ANDROID_JAR:?}"; : "${BT:?}"
rm -rf build && mkdir -p build/classes
"$BT/aapt2" link -o build/base.apk --manifest AndroidManifest.xml -I "$ANDROID_JAR" --min-sdk-version 30 --target-sdk-version 30
javac --release 17 -cp "$ANDROID_JAR" -d build/classes $(find src -name '*.java')
"$BT/d8" --min-api 30 --lib "$ANDROID_JAR" --output build $(find build/classes -name '*.class')
(cd build && cp base.apk unsigned.apk && zip -q unsigned.apk classes.dex)
"$BT/zipalign" -f 4 build/unsigned.apk build/aligned.apk
KS=debug.keystore  # kept in the repo so updates install over the previous version
[ -f $KS ] || keytool -genkeypair -keystore $KS -storepass android -keypass android -alias s20 -keyalg RSA -keysize 2048 \
  -validity 10000 -dname "CN=s20ui" >/dev/null 2>&1
"$BT/apksigner" sign --ks $KS --ks-pass pass:android --key-pass pass:android --out s20ui.apk build/aligned.apk
rm -rf build
echo "built $(pwd)/s20ui.apk ($(stat -c %s s20ui.apk) bytes)"
