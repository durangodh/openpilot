#!/bin/bash
# Stage 6 check: camera bridge -> s20camerad -> modeld (demo car) -> modelV2, all on the phone.
# First start the camera on the Android side (PC window):
#   adb shell su -c "sh /data/s20pilot/rootfs/root/openpilot/s20/android/s20cam.sh auto ctrl=off"
# Then in the chroot:  cd /root/openpilot && bash s20/tests/stage6_pipeline.sh
cd "$(dirname "$0")/../.." || exit 1
source .venv/bin/activate || exit 1
export S20=1 PYTHONPATH=$PWD
LOGS=/root/s20logs; mkdir -p $LOGS
[ -f /s20share/road.ring ] || { echo "FAIL: no /s20share/road.ring, start s20cam first"; exit 1; }

python -m s20.camerad.s20camerad > $LOGS/s20camerad.log 2>&1 &
CAMD=$!
python -m openpilot.selfdrive.modeld.modeld --demo > $LOGS/modeld.log 2>&1 &
MODELD=$!
trap 'kill $CAMD $MODELD 2>/dev/null' EXIT
echo "started s20camerad ($CAMD) and modeld ($MODELD); waiting for the model to load..."

python - <<'PY'
import time
from openpilot.cereal import messaging
sm = messaging.SubMaster(["narrowRoadCameraState", "modelV2"])
t_end = time.monotonic() + 90
while time.monotonic() < t_end and sm.recv_frame["modelV2"] == 0:
  sm.update(100)
if sm.recv_frame["modelV2"] == 0:
  print("FAIL: no modelV2 within 90 s (see /root/s20logs/modeld.log and s20camerad.log)")
  raise SystemExit(1)
print("modelV2 is coming; measuring 20 s")
n_cam = n_model = 0; exec_t = []; drop = []
t0 = time.monotonic()
while time.monotonic() - t0 < 20:
  sm.update(100)
  n_cam += sm.updated["narrowRoadCameraState"]
  if sm.updated["modelV2"]:
    n_model += 1
    exec_t.append(sm["modelV2"].modelExecutionTime * 1000)
    drop.append(sm["modelV2"].frameDropPerc)
dt = time.monotonic() - t0
lane = sm["modelV2"].laneLineProbs
print(f"camera {n_cam / dt:.1f} Hz, modelV2 {n_model / dt:.1f} Hz")
print(f"model execution {sum(exec_t) / len(exec_t):.1f} ms avg, {max(exec_t):.1f} max; frame drop {sum(drop) / len(drop):.1f} %")
print("lane line probabilities:", [round(x, 2) for x in lane])
print("PASS" if n_model / dt > 15 else "SLOW: modelV2 under 15 Hz")
PY
rc=$?
echo "--- last modeld log lines:"; tail -5 $LOGS/modeld.log
exit $rc
