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
  env S20_REG_LIMIT=${S20_REG_LIMIT:-64} S20_SKIP_RE="$S20_SKIP_RE" S20_STACK_SHR=${S20_STACK_SHR:-11} S20_PVT_FIBERS=${S20_PVT_FIBERS:-4096} S20_PERWAVE=${S20_PERWAVE:-auto} DEV=QCOM:IR3 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 DEBUG=2 $cfg timeout 1200 python3 - "$MODEL" > "$log" 2>&1 <<'EOF'
import sys, time, numpy as np
from tinygrad import Tensor, Device, dtypes
from tinygrad.nn.onnx import OnnxRunner
# print every QCOM kernel as it is encoded, before it runs, so a hang names its kernel
from tinygrad.runtime import ops_qcom as oq
_exec = oq.QCOMComputeQueue.exec
def _trace(self, call, prg):
  d, _ = oq.qcom_build_program(self.dev, prg, self.devs)
  import os, re
  _k = getattr(_trace, "n", 0); _trace.n = _k + 1
  name = re.sub(r"\x1b\[[0-9;]*m", "", str(getattr(prg.src[0].arg, "name", "?")))
  params = ",".join(str(getattr(q, "dtype", "?")).replace("dtypes.", "") for q in getattr(prg, "kernel_params", []))
  print(f"S20N {_k} {name} params=[{params}]", flush=True)
  if _k < 3:
    try:
      import pickle; pickle.dump(prg, open(f"/root/s20logs/k{_k}.pkl", "wb"))
    except Exception as e: print("S20N pickle failed:", e, flush=True)
  print(f"S20K {name} gs={prg.arg.global_size} ls={prg.arg.local_size} "
        f"fregs={d.fregs} hregs={d.hregs} pvt={d.pvtmem} shm={d.shmem} brn={d.brnchstck} img={d.image_size} "
        f"tex={d.tex_cnt} ibo={d.ibo_cnt} samp={d.samp_cnt} maxthr={d.max_threads} merged={getattr(d,'mergedregs','?')} "
        f"perwave={getattr(d,'pvt_per_wave','?')} preload={getattr(d,'preload_units','?')} stack={getattr(d,'hw_stack_offset_reg','?')} tsize={getattr(d,'threadsize','?')} pvttot={getattr(d,'pvtmem_size_total','?')}", flush=True)
  import os, re
  if os.environ.get("S20_SKIP_RE") and re.search(os.environ["S20_SKIP_RE"], name):
    print(f"S20SKIP {name}", flush=True)   # not sent to the GPU: results are wrong, only to find which kernel hangs
    return None
  return _exec(self, call, prg)
oq.QCOMComputeQueue.exec = _trace
# one GPU submission per kernel (DEBUG=2 waits after each), so the last kernel printed is the one before the hang
import itertools
from tinygrad.runtime.support import hcq2 as h
def _one_by_one(l, profile):
  srcs = []
  for hcq, grp in itertools.groupby(zip(l.src, [h.get_enqueue_devs(c) for c in l.src]), key=lambda e: bool(e[1])):
    run = list(grp)
    if not hcq: srcs += [c for c, _ in run]; continue
    for c, d in run:
      q = "COMPUTE:0" if c.body.op is h.Ops.PROGRAM else "ENCDEC:0" if c.body.op is h.Ops.CUSTOM_FUNCTION else "COPY:0"
      srcs.append(h._finalize_batch(h.BatchCtx([(c, d[0], q)], profile), False))
  return l.replace(src=tuple(srcs))
h.sched_batches = _one_by_one
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
    echo "   OK: $(grep S20RUN "$log" | tail -1)  $(grep S20OUT "$log")  skipped: $(grep -c S20SKIP "$log")"
  else
    echo "   FAIL (rc=$rc): $(grep -m1 -E 'hang detected|timed out|Error' "$log")  skipped: $(grep -c S20SKIP "$log")  kernels sent: $(grep -c S20K "$log")"
    echo "   last kernels before the failure:"
    sed 's/\x1b\[[0-9;]*m//g' "$log" | grep -E "^\*\*\* " | tail -3 | cut -c1-110 | sed 's/^/     /'
    fin=$(sed 's/\x1b\[[0-9;]*m//g' "$log" | grep -E '^\*\*\* QCOM' | grep -vc copy)
    echo "   GPU kernels finished: $fin -> the hanging kernel is #$fin:"
    grep "^S20N $fin " "$log" | cut -c1-200 | sed 's/^/     /'
    grep "^S20K" "$log" | sed -n "$((fin+1))p" | cut -c1-260 | sed 's/^/     /'
  fi
}

if [ -n "$1" ]; then run_one "$1"; else for n in "${!CONFIGS[@]}"; do run_one "$n"; done; fi
