#!/bin/bash
# Stage 7b: openpilot on the S20 screen. Runs s20camerad + modeld (demo car) + s20uid until Ctrl+C.
# Before: the camera bridge must be running and the app installed (see s20/ui/README.md).
cd "$(dirname "$0")/../.." || exit 1
source .venv/bin/activate || exit 1
export S20=1 PYTHONPATH=$PWD
L=/root/s20logs; mkdir -p $L
[ -f /s20share/road.ring ] || { echo "FAIL: start s20cam first"; exit 1; }
rm -f /s20share/wide.ring
python -m s20.camerad.s20camerad > $L/s20camerad.log 2>&1 & P1=$!
python -m openpilot.selfdrive.modeld.modeld --demo > $L/modeld.log 2>&1 & P2=$!
python -m s20.ui.s20uid > $L/s20uid.log 2>&1 & P3=$!
trap 'kill $P1 $P2 $P3 2>/dev/null; echo stopped' EXIT INT TERM
echo "running s20camerad, modeld, s20uid; open the 's20 openpilot' app on the phone. Ctrl+C to stop."
while sleep 5; do
  printf "\rui.json age: %ss   " "$(python3 -c "import os,time;print(round(time.time()-os.path.getmtime('/s20share/ui.json'),1))" 2>/dev/null || echo '-')"
done
