#!/bin/bash
# Stage 5c: tune the driving model's GPU kernels on this phone with tinygrad's BEAM search (times real candidates
# and keeps the fastest). Candidates the a650 hangs on (register spill / >64 registers) are rejected before timing.
# Long: 20-90 min. Results are cached (~/.cache/tinygrad), so later builds reuse them.
#   bash s20/tests/stage5_beam.sh [beam width, default 2]
cd "$(dirname "$0")/../.." || exit 1
source .venv/bin/activate || exit 1
bash s20/apply_patches.sh >/dev/null || exit 1
MODELS=$PWD/openpilot/selfdrive/modeld/models
export PYTHONPATH="$PWD/tinygrad_repo:$PWD"
B=${1:-2}
cp -f $MODELS/driving_tinygrad.pkl /root/s20logs/driving_tinygrad.nobeam.pkl 2>/dev/null
echo "== BEAM=$B on driving_supercombo.onnx ($(date +%H:%M))"
env S20_REG_LIMIT=64 DEV=QCOM:IR3 IMAGE=0 FLOAT16=1 NOLOCALS=1 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 BEAM=$B \
  python "$PWD/tinygrad_repo/examples/openpilot/compile_onnx.py" "$MODELS/driving_supercombo.onnx" \
  "$MODELS/driving_tinygrad.pkl" --out-of-band --benchmark-runs 10 2>&1 | grep -vE "^\s*$" | tail -15
rc=${PIPESTATUS[0]}
echo "== done rc=$rc ($(date +%H:%M))"
[ $rc = 0 ] && echo "PASS (old model kept in /root/s20logs/driving_tinygrad.nobeam.pkl)" || {
  echo "FAIL: restoring the previous model"; cp -f /root/s20logs/driving_tinygrad.nobeam.pkl $MODELS/driving_tinygrad.pkl; }
exit $rc
