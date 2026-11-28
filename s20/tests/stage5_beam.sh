#!/bin/bash
# Stage 5c: tune the driving model's GPU kernels on this phone with tinygrad's BEAM search (times real candidates
# and keeps the fastest). Candidates the a650 hangs on (register spill / >64 registers) are rejected before timing.
# Long (hours). Results are cached (~/.cache/tinygrad) per kernel, so a stopped run continues where it was.
#   bash s20/tests/stage5_beam.sh          search (BEAM=2) every kernel not tuned yet
#   bash s20/tests/stage5_beam.sh cache    no search: tuned kernels + usual heuristics for the rest (minutes), prints the time
# Builds (scons) use the tuned kernels the same way as "cache".
cd "$(dirname "$0")/../.." || exit 1
source .venv/bin/activate || exit 1
bash s20/apply_patches.sh >/dev/null || exit 1
MODELS=$PWD/openpilot/selfdrive/modeld/models
export PYTHONPATH="$PWD/tinygrad_repo:$PWD"
B=2  # the cache is per beam width: keep 2
CACHE_ONLY=""; [ "$1" = "cache" ] && CACHE_ONLY="S20_BEAM_CACHE_ONLY=1"
TUNED=$(python3 -c "import sqlite3;db=sqlite3.connect('/root/.cache/tinygrad/cache.db');print(sum(db.execute(f'select count(*) from \"{t}\"').fetchone()[0] for (t,) in db.execute(\"select name from sqlite_master where type='table'\") if 'beam' in t))" 2>/dev/null)
echo "== tuned kernels in the cache: ${TUNED:-0}"
[ -f /root/s20logs/driving_tinygrad.nobeam.pkl ] || cp -f $MODELS/driving_tinygrad.pkl /root/s20logs/driving_tinygrad.nobeam.pkl 2>/dev/null  # first, untuned model
echo "== BEAM=$B ${CACHE_ONLY:+(tuned kernels only) }on driving_supercombo.onnx ($(date +%H:%M))"
env $CACHE_ONLY S20_REG_LIMIT=64 DEV=QCOM:IR3 IMAGE=0 FLOAT16=1 NOLOCALS=1 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 BEAM=$B \
  python "$PWD/tinygrad_repo/examples/openpilot/compile_onnx.py" "$MODELS/driving_supercombo.onnx" \
  "$MODELS/driving_tinygrad.pkl" --out-of-band --benchmark-runs 10 2>&1 | grep -vE "^\s*$" | tail -15
rc=${PIPESTATUS[0]}
echo "== done rc=$rc ($(date +%H:%M))"
[ $rc = 0 ] && echo "PASS (old model kept in /root/s20logs/driving_tinygrad.nobeam.pkl)" || {
  echo "FAIL: the previous model file is unchanged unless compile_onnx wrote it; run: bash s20/build.sh"; }
exit $rc
