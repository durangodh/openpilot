#!/bin/bash
# Stage 5c: tune the driving model's GPU kernels on this phone with tinygrad's BEAM search (times real candidates
# and keeps the fastest). Candidates the a650 hangs on (register spill / >64 registers) are rejected before timing.
# Long (hours). Results are cached (~/.cache/tinygrad) per kernel, so a stopped run continues where it was.
#   bash s20/tests/stage5_beam.sh          search (BEAM=2) every kernel not tuned yet
#   bash s20/tests/stage5_beam.sh cache    no search: tuned kernels + usual heuristics for the rest (minutes), prints the time
# Each BEAM kernel is timed at full size against the usual heuristic one and only kept when faster (S20_BEAM_VERIFY):
# lines "s20 verify <kernel>: beam .. ms, heuristic .. ms -> ..." show the comparison.
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
cp -f $MODELS/driving_tinygrad.pkl /root/s20logs/driving_tinygrad.prev.pkl 2>/dev/null  # restored if the result is slow
# a candidate kernel can hang the a650: the GPU wait gives up after 20 s and exits (rc 75), the candidate is
# remembered as bad (~/.cache/tinygrad/s20_beam_bad.txt) and the run starts again from the cache
for try in $(seq 1 40); do
  echo "== BEAM=$B ${CACHE_ONLY:+(tuned kernels only) }on driving_supercombo.onnx ($(date +%H:%M), run $try)"
  env $CACHE_ONLY S20_BEAM_VERIFY=1 S20_BEAM_GUARD=1 S20_WAIT_MAX_S=20 S20_REG_LIMIT=64 DEV=QCOM:IR3 IMAGE=0 FLOAT16=1 NOLOCALS=1 \
    JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1 BEAM=$B \
    python "$PWD/tinygrad_repo/examples/openpilot/compile_onnx.py" "$MODELS/driving_supercombo.onnx" \
    "$MODELS/driving_tinygrad.pkl" --out-of-band --benchmark-runs 10 2>&1 | tee /root/s20logs/beam_last.log | grep -vE "^\s*$" | grep -vE "^compiling|it/s" | tail -40
  rc=${PIPESTATUS[0]}
  [ $rc = 0 ] && break
  echo "== run $try ended rc=$rc: restarting (bad candidates: $(wc -l < ~/.cache/tinygrad/s20_beam_bad.txt 2>/dev/null || echo 0))"
  sleep 5
done
echo "== done rc=$rc ($(date +%H:%M))"
# a tuned model must not be slower than the untuned one (~60 ms; S20_MAX_MS, default 70): otherwise keep the previous model
MS=$(grep -oE "\[[0-9]+/10\] [0-9.]+ ms" /root/s20logs/beam_last.log 2>/dev/null | tail -1 | awk '{print int($2)}')
if [ $rc = 0 ] && [ -n "$MS" ] && [ "$MS" -gt ${S20_MAX_MS:-70} ]; then
  echo "SLOW: ${MS} ms per run with BEAM. Previous model restored"
  cp -f /root/s20logs/driving_tinygrad.prev.pkl $MODELS/driving_tinygrad.pkl
  rc=1
fi
[ $rc = 0 ] && echo "PASS ${MS} ms (previous model kept in /root/s20logs/driving_tinygrad.prev.pkl)" || {
  echo "FAIL: the previous model file is unchanged unless compile_onnx wrote it; run: bash s20/build.sh"; }
exit $rc
