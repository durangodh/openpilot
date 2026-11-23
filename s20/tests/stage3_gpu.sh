#!/bin/bash
# Stage 3: does tinygrad's QCOM backend run on the S20 FE's Adreno 650?
# Run inside the chroot, from the openpilot checkout root.
#   bash s20/tests/stage3_gpu.sh

cd "$(dirname "$0")/../.." || exit 1
export PYTHONPATH="$PWD/tinygrad_repo:$PYTHONPATH"

echo "== kgsl device"
ls -l /dev/kgsl-3d0 || { echo "FAIL: no /dev/kgsl-3d0"; exit 1; }

echo "== tinygrad QCOM"
DEV=QCOM python3 - <<'EOF'
import time
from tinygrad import Tensor, Device
dev = Device[Device.DEFAULT]
print("device:", Device.DEFAULT, "gpu_id:", getattr(dev, "gpu_id", "?"))

out = (Tensor.ones(4) + 1).tolist()
assert out == [2.0] * 4, out
print("add ok:", out)

a, b = Tensor.rand(512, 512).realize(), Tensor.rand(512, 512).realize()
(a @ b).realize()
t = time.perf_counter()
for _ in range(10): (a @ b).realize()
Device[Device.DEFAULT].synchronize()
print(f"matmul 512x512: {(time.perf_counter()-t)/10*1000:.2f} ms")
print("PASS")
EOF
