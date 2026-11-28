#!/bin/bash
# Stage 5 debug: find which tinygrad option makes the driving model hang the Adreno 650.
# Run inside the chroot, from the openpilot checkout root, after stage5_model.sh fetched the models.
#   bash s20/tests/stage5_debug.sh            # all configs, simplest first
#   bash s20/tests/stage5_debug.sh 2          # one config by number
#
# Each config runs the model once with DEBUG=2 (tinygrad prints every kernel as it runs it), with a
# time limit. Full logs: /root/s20logs/cfgN.log. On a hang the last kernel lines are printed.

cd "$(dirname "$0")/../.." || exit 1
ROOT=$PWD
MODEL=$ROOT/openpilot/selfdrive/modeld/models/driving_supercombo.onnx
export PYTHONPATH="$ROOT/tinygrad_repo:$ROOT:$PYTHONPATH"
LOGS=/root/s20logs
mkdir -p $LOGS
bash s20/apply_patches.sh >/dev/null || exit 1

CONFIGS=(
  "IMAGE=0 FLOAT16=0"
  "IMAGE=0 FLOAT16=1"
  "IMAGE=0 FLOAT16=1 NOLOCALS=1"
  "IMAGE=1 FLOAT16=1 NOLOCALS=1"
)

run_one() {  # run_one <n>
  n=$1; cfg=${CONFIGS[$n]}; log=$LOGS/cfg$n.log
  echo "== [$n] $cfg"
  env DEV=QCOM:IR3 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 DEBUG=2 $cfg timeout 1200 python3 - "$MODEL" > "$log" 2>&1 <<'EOF'
import sys, time, numpy as np
from tinygrad import Tensor, Device, dtypes
from tinygrad.nn.onnx import OnnxRunner
# print every QCOM kernel as it is encoded, before it runs, so a hang names its kernel
from tinygrad.runtime import ops_qcom as oq
_exec = oq.QCOMComputeQueue.exec
def _trace(self, call, prg):
  d, _ = oq.qcom_build_program(self.dev, prg, self.devs)
  print(f"S20K {prg.arg.name if hasattr(prg.arg,'name') else '?'} gs={prg.arg.global_size} ls={prg.arg.local_size} "
        f"fregs={d.fregs} hregs={d.hregs} pvt={d.pvtmem} shm={d.shmem} brn={d.brnchstck} img={d.image_size} "
        f"tex={d.tex_cnt} ibo={d.ibo_cnt} samp={d.samp_cnt} maxthr={d.max_threads} merged={getattr(d,'mergedregs','?')} "
        f"perwave={getattr(d,'pvt_per_wave','?')} preload={getattr(d,'preload_units','?')} stack={getattr(d,'hw_stack_offset_reg','?')}", flush=True)
  return _exec(self, call, prg)
oq.QCOMComputeQueue.exec = _trace
runner = OnnxRunner(sys.argv[1])
def arr(t):
  dt = np.float16 if t.dtype == dtypes.half else np.float32 if dtypes.is_float(t.dtype) else np.uint8
  return Tensor(np.random.default_rng(0).standard_normal(t.shape).astype(dt) if dtypes.is_float(t.dtype) else np.zeros(t.shape, dt))
ins = {k: arr(t) for k, t in runner.graph_inputs.items()}
for i in range(2):
  st = time.perf_counter()
  out = runner({k: v.to(Device.DEFAULT) for k, v in ins.items()})
  Tensor.realize(*out.values()); Device[Device.DEFAULT].synchronize()
  print(f"S20RUN {i} {(time.perf_counter()-st)*1e3:.1f} ms", flush=True)
o = next(iter(out.values())).numpy()
print(f"S20OUT finite={np.isfinite(o).all()} mean={float(np.abs(o).mean()):.4f}", flush=True)
EOF
  rc=$?
  if grep -q "S20OUT" "$log"; then
    echo "   OK: $(grep S20RUN "$log" | tail -1)  $(grep S20OUT "$log")"
  else
    echo "   FAIL (rc=$rc): $(grep -m1 -E 'hang detected|timed out|Error' "$log")"
    echo "   last kernels before the failure:"
    grep -E "^\*\*\*|^ *[0-9]+ .*(r_|E_)" "$log" | tail -2 | cut -c1-160 | sed 's/^/     /'
    echo "   kernel(s) sent to the GPU last:"
    grep "^S20K" "$log" | tail -3 | cut -c1-230 | sed 's/^/     /'
  fi
}

if [ -n "$1" ]; then run_one "$1"; else for n in "${!CONFIGS[@]}"; do run_one "$n"; done; fi
