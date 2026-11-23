#!/bin/bash
# Stage 4: is the panda visible over OTG and does CAN come in?
# Run inside the chroot, from the openpilot checkout root, car on (ACC/IGN).
#   bash s20/tests/stage4_panda.sh

cd "$(dirname "$0")/../.." || exit 1
export PYTHONPATH="$PWD:$PWD/panda:$PWD/opendbc_repo:$PYTHONPATH"

echo "== USB devices"
lsusb || echo "(install usbutils)"

echo "== panda"
python3 - <<'EOF'
import time
from panda import Panda
serials = Panda.list()
print("pandas:", serials)
if not serials:
    raise SystemExit("FAIL: no panda")
p = Panda(serials[0])
print("hw type:", p.get_type(), "fw:", p.get_version())
p.set_safety_mode()  # default = silent: receive only, never transmit
counts = {}
t = time.time()
while time.time() - t < 3:
    for msg in p.can_recv():
        bus = msg[2]
        counts[bus] = counts.get(bus, 0) + 1
print("CAN frames in 3 s per bus:", counts)
print("PASS" if counts else "FAIL: no CAN")
EOF
