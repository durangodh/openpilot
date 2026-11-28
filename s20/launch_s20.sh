#!/bin/bash
# openpilot on the Galaxy S20 FE, inside the Ubuntu chroot. Started at boot by s20/android/autostart.sh (Android side).
#   bash s20/launch_s20.sh            manager: openpilot like a comma 3 (offroad home screen, onroad with the panda + car)
#   bash s20/launch_s20.sh demo       no panda: camera + driving model (demo car) + screen data, to test the screen
cd "$(dirname "$0")/.." || exit 1
source launch_env.sh
source .venv/bin/activate || { echo "launch_s20: no .venv, run s20/build.sh first"; exit 1; }
export S20=1 PYTHONPATH=$PWD S20_MODE=${1:-manager}

# the camera bridge (s20cam, Android side) makes road.ring; s20camerad waits for fresh frames itself
for i in $(seq 60); do [ -f /s20share/road.ring ] && break; sleep 1; done
rm -f /s20share/wide.ring  # the wide camera is off; a stale ring would make modeld wait for it
echo "launch_s20: $(date '+%F %T') mode $S20_MODE"

case "$S20_MODE" in
  demo)
    trap 'kill $(jobs -p) 2>/dev/null' EXIT INT TERM
    python -m s20.camerad.s20camerad &
    python -m openpilot.selfdrive.modeld.modeld --demo &
    python -m s20.ui.s20uid &
    wait -n
    echo "launch_s20: a demo process exited"
    exit 1
    ;;
  *)
    # no comma connect registration on the S20: skip it (it would wait for the network)
    python -c "
from openpilot.common.params import Params
p = Params()
if p.get('DongleId') is None:
  p.put('DongleId', 'UnregisteredDevice')"
    # main() directly: the __main__ path opens an error window on a display the S20 does not have
    exec python -c "from openpilot.system.manager.manager import main; main()"
    ;;
esac
