#!/usr/bin/env sh
# Build and flash the Genesis DH MM-CAN gateway firmware (safety/safety_mcan_gateway.h)
# onto the SPARE black panda. Connect ONLY the spare panda over USB (stop openpilot
# first, or unplug the main panda): this refuses to run with more than one panda.
set -e
cd "$(dirname "$0")"
MCAN_GATEWAY=1 scons -u -j$(nproc) obj/panda_mcan_gw.bin.signed
PYTHONPATH=.. python3 - <<'PY'
from python import Panda
serials = Panda.list()
if len(serials) != 1:
  raise SystemExit(f"connect exactly one panda (the spare one), found {len(serials)}: {serials}")
p = Panda(serials[0])
print("flashing MM-CAN gateway to", serials[0])
p.flash("obj/panda_mcan_gw.bin.signed")
PY
