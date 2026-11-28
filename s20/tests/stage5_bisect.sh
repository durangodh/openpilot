#!/bin/bash
# Stage 5 debug: which BEAM-tuned kernel makes the driving model slow (9.4 s/run instead of ~60 ms)?
# Compiles the model with only the first N BEAM kernels (S20_BEAM_N) and halves the range until one kernel is left.
# Does not touch the model openpilot uses (writes /tmp/s20_bisect.pkl). ~4 min per step, about 8 steps.
#   bash s20/tests/stage5_bisect.sh
#   bash s20/tests/stage5_bisect.sh verify 6    time BEAM kernels 1..6 alone at full size, BEAM vs heuristic, and the model
cd "$(dirname "$0")/../.." || exit 1
source .venv/bin/activate || exit 1
bash s20/apply_patches.sh >/dev/null || exit 1
export PYTHONPATH="$PWD/tinygrad_repo:$PWD"
L=/root/s20logs/bisect; mkdir -p $L
SLOW_MS=${S20_MAX_MS:-70}

run() {  # run <n>: prints ms per run with the first n BEAM kernels
  env S20_BEAM_N=$1 BEAM=2 S20_BEAM_CACHE_ONLY=1 S20_WAIT_MAX_S=60 S20_REG_LIMIT=64 DEV=QCOM:IR3 IMAGE=0 FLOAT16=1 \
    NOLOCALS=1 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 python tinygrad_repo/examples/openpilot/compile_onnx.py \
    openpilot/selfdrive/modeld/models/driving_supercombo.onnx /tmp/s20_bisect.pkl --out-of-band --benchmark-runs 3 \
    > $L/n$1.log 2>&1
  grep -oE "\[[0-9]+/3\] [0-9.]+ ms" $L/n$1.log | tail -1 | awk '{print int($2)}'
}

if [ "$1" = "verify" ]; then
  n=${2:-6}
  env S20_BEAM_VERIFY=2 S20_BEAM_N=$n BEAM=2 S20_BEAM_CACHE_ONLY=1 S20_WAIT_MAX_S=60 S20_REG_LIMIT=64 DEV=QCOM:IR3 IMAGE=0 \
    FLOAT16=1 NOLOCALS=1 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 python tinygrad_repo/examples/openpilot/compile_onnx.py \
    openpilot/selfdrive/modeld/models/driving_supercombo.onnx /tmp/s20_bisect.pkl --out-of-band --benchmark-runs 3 \
    > $L/verify$n.log 2>&1
  echo "== each kernel alone at full size (ms):"
  tr '\r' '\n' < $L/verify$n.log | grep -oE "s20 (verify|beam) #[0-9]+ .*" | grep -v "not used"
  echo "== whole model with them:"; grep -oE "\[[0-9]+/3\] [0-9.]+ ms" $L/verify$n.log | tail -1
  exit 0
fi
TOTAL=$(python3 -c "import sqlite3;db=sqlite3.connect('/root/.cache/tinygrad/cache.db');print(sum(db.execute(f'select count(*) from \"{t}\"').fetchone()[0] for (t,) in db.execute(\"select name from sqlite_master where type='table'\") if t.startswith('beam_search')))")
echo "== $TOTAL BEAM kernels in the cache; slow = more than $SLOW_MS ms per run ($(date +%H:%M))"
ms=$(run 0); echo "N=0 (no BEAM kernel): ${ms:-error} ms"
if [ -z "$ms" ] || [ "$ms" -gt $SLOW_MS ]; then
  echo "RESULT: slow even without BEAM kernels -> BEAM=2 itself changes something (log $L/n0.log)"; exit 1
fi
ms=$(run $TOTAL); echo "N=$TOTAL (all): ${ms:-error} ms"
[ -n "$ms" ] && [ "$ms" -le $SLOW_MS ] && { echo "RESULT: not slow with all BEAM kernels"; exit 0; }
lo=0; hi=$TOTAL   # fast with lo kernels, slow with hi
while [ $((hi - lo)) -gt 1 ]; do
  mid=$(( (lo + hi) / 2 ))
  ms=$(run $mid); echo "N=$mid: ${ms:-error} ms ($(date +%H:%M))"
  if [ -n "$ms" ] && [ "$ms" -le $SLOW_MS ]; then lo=$mid; else hi=$mid; fi
done
echo "RESULT: BEAM kernel #$hi makes the model slow:"
tr '\r' '\n' < $L/n$hi.log | grep -o "s20 beam #$hi:.*" | head -1
echo "(fast with the first $lo: S20_BEAM_N=$lo)"
