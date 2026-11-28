#!/bin/bash
# Build s20cam.jar (dex) for app_process. Needs a JDK, android.jar (platform 35) and d8 (build-tools 35):
#   ANDROID_JAR=.../android-35/android.jar D8=.../build-tools/d8 bash s20/android/build.sh
set -e
cd "$(dirname "$0")"
: "${ANDROID_JAR:?set ANDROID_JAR}"; : "${D8:?set D8}"
rm -rf build && mkdir -p build/classes
javac --release 17 -cp "$ANDROID_JAR" -d build/classes src/s20cam/*.java
"$D8" --min-api 30 --lib "$ANDROID_JAR" --output build $(find build/classes -name '*.class')
(cd build && rm -f ../s20cam.jar && zip -q ../s20cam.jar classes.dex)
echo "built $(pwd)/s20cam.jar ($(stat -c %s s20cam.jar) bytes)"
