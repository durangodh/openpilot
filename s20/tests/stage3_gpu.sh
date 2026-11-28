#!/bin/bash
# Stage 3: does tinygrad's QCOM backend run on the S20 FE's Adreno 650?
# Run inside the chroot, from the openpilot checkout root.
#   bash s20/tests/stage3_gpu.sh
#
# tinygrad's QCOM device has two renderers:
#   - QCOMCL: Qualcomm's OpenCL compiler, hard-wired to a630 (comma 3/3X, SD845) -> fails on a650
#   - IR3:    Mesa's freedreno ir3 compiler (pip package tinymesa) -> the one the S20 FE needs
# The device falls back to the next renderer when one fails, but we pin IR3 to be explicit.

cd "$(dirname "$0")/../.." || exit 1
export PYTHONPATH="$PWD/tinygrad_repo:$PYTHONPATH"

echo "== s20 patches (tinygrad a650)"
bash s20/apply_patches.sh || exit 1

echo "== kgsl device"
ls -l /dev/kgsl-3d0 || { echo "FAIL: no /dev/kgsl-3d0"; exit 1; }

echo "== tinymesa (Mesa ir3 compiler)"
python3 -c "import tinymesa" 2>/dev/null || \
  pip3 install --break-system-packages tinymesa==25.2.7.2 || { echo "FAIL: pip install tinymesa"; exit 1; }

echo "== clang (tinygrad's CPU device; reading results back copies to CPU)"
command -v clang >/dev/null || apt install -y clang || { echo "FAIL: apt install clang"; exit 1; }

echo "== tinygrad QCOM:IR3"
DEV=QCOM:IR3 python3 - <<'EOF'
import time
from tinygrad import Tensor, Device
dev = Device[Device.DEFAULT]
print("device:", Device.DEFAULT, "gpu_id:", getattr(dev, "gpu_id", "?"), "renderer:", type(dev.renderer).__name__)

out = (Tensor.ones(4) + 1).tolist()
assert out == [2.0] * 4, out
print("add ok:", out)

a, b = Tensor.rand(512, 512).realize(), Tensor.rand(512, 512).realize()
(a @ b).realize()  # warm-up (compile)
t = time.perf_counter()
for _ in range(10): (a @ b).realize()
dev.synchronize()
print(f"matmul 512x512: {(time.perf_counter()-t)/10*1000:.2f} ms")
print("PASS")
EOF
