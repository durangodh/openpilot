#!/bin/bash
# Stage 7a check: accelerometer/gyroscope from the phone reach openpilot in the device frame.
# Start the bridge first (PC window):  adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/s20cam.sh auto ctrl=off"
# Hold the phone as it will be mounted (landscape, screen to you, back camera forward) and keep it still at the start.
#   bash s20/tests/stage7_imu.sh        (S20_IMU_RECAL=1 to redo the mount detection)
cd "$(dirname "$0")/../.." || exit 1
source .venv/bin/activate || exit 1
export PYTHONPATH=$PWD
[ -f /s20share/imu.ring ] || { echo "FAIL: no /s20share/imu.ring, start s20cam (new version) first"; exit 1; }
python -m s20.sensord.s20sensord > /root/s20logs/s20sensord.log 2>&1 &
PID=$!; trap 'kill $PID 2>/dev/null' EXIT
python - <<'PY'
import time, numpy as np
from openpilot.cereal import messaging
sm = messaging.SubMaster(["accelerometer", "gyroscope"])
acc, gyr, t0 = [], [], time.monotonic()
while time.monotonic() - t0 < 8:
  sm.update(50)
  if sm.updated["accelerometer"]: acc.append(list(sm["accelerometer"].acceleration.v))
  if sm.updated["gyroscope"]: gyr.append(list(sm["gyroscope"].gyroUncalibrated.v))
dt = time.monotonic() - t0
if not acc: raise SystemExit("FAIL: no accelerometer messages (see /root/s20logs/s20sensord.log)")
a = np.mean(acc, 0); g = np.mean(gyr, 0) if gyr else np.zeros(3)
print(f"accelerometer {len(acc)/dt:.0f} Hz, gyroscope {len(gyr)/dt:.0f} Hz")
print(f"mean accel [fwd, right, down] = {np.round(a, 2)}  (expect about [0, 0, -9.8] when level)")
print(f"mean gyro  [fwd, right, down] = {np.round(g, 3)} rad/s (expect about 0 when still)")
ok = len(acc)/dt > 50 and abs(a[2] + 9.8) < 1.5
print("PASS" if ok else "CHECK: rate or orientation off")
PY
echo "--- s20sensord log:"; head -3 /root/s20logs/s20sensord.log
