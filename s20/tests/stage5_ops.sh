#!/bin/bash
# Stage 5 op check: tiny GPU kernels with the dtypes the driving model's first kernels use.
# Each case runs in its own process with a time limit, so a GPU hang names the case.
# Run inside the chroot, from the openpilot checkout root.
#   bash s20/tests/stage5_ops.sh
# Logs: /root/s20logs/op_<name>.log

cd "$(dirname "$0")/../.." || exit 1
export PYTHONPATH="$PWD/tinygrad_repo:$PYTHONPATH"
LOGS=/root/s20logs; mkdir -p $LOGS
bash s20/apply_patches.sh >/dev/null || exit 1

run() {  # run <name> <python expression producing a Tensor> <expected python list or None>
  name=$1; expr=$2
  DEV=QCOM:IR3 DEBUG=0 timeout 90 python3 - "$expr" > $LOGS/op_$name.log 2>&1 <<'EOF'
import sys, numpy as np
from tinygrad import Tensor, dtypes
gpu = eval(sys.argv[1]).realize()
r = gpu.numpy()
print("S20OK", r.dtype, r.shape, r.reshape(-1)[:6])
EOF
  if grep -q S20OK $LOGS/op_$name.log; then echo "OK    $name: $(grep S20OK $LOGS/op_$name.log | cut -c6-120)"
  else echo "FAIL  $name: $(grep -m1 -E 'hang|timed out|Error' $LOGS/op_$name.log | cut -c1-120)"; fi
}

run f32_add      'Tensor.arange(512).float() * 2 + 1'
run f32_ls128    '(Tensor.arange(512).float().reshape(4, 128) * 3).sum(1)'
run u8_to_f32    'Tensor(np.arange(512) % 256, dtype=dtypes.uint8).float() * 2'
run u8_add       'Tensor(np.arange(512) % 256, dtype=dtypes.uint8) + 1'
run f16_mul      'Tensor.arange(512).half() * 2'
run u8_to_f16    'Tensor(np.arange(512) % 256, dtype=dtypes.uint8).half() / 255'
run f16_to_f32   '(Tensor.arange(512).half() * 0.5).float()'
run i16_ops      'Tensor(np.arange(512), dtype=dtypes.int16) * 3 - 7'
run bool_where   '(Tensor.arange(512) > 100).where(Tensor.arange(512).float(), 0)'
run u8_reduce    'Tensor(np.arange(512) % 256, dtype=dtypes.uint8).reshape(4, 128).float().sum(1)'
run yuv_like     '(Tensor(np.arange(6*128*256) % 256, dtype=dtypes.uint8).reshape(6, 128, 256).float() / 255).reshape(6, 64, 2, 128, 2).mean((2, 4))'
