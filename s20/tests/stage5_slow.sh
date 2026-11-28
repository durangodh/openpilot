#!/bin/bash
# Stage 5 debug: which GPU kernels of the driving model are slow? Times every kernel (DEBUG=2) and lists the slowest.
# Does not touch the model openpilot uses (writes /tmp/s20_slow.pkl).
#   bash s20/tests/stage5_slow.sh beam      with the BEAM-tuned kernels (cache)
#   bash s20/tests/stage5_slow.sh nobeam    usual heuristics only
cd "$(dirname "$0")/../.." || exit 1
source .venv/bin/activate || exit 1
bash s20/apply_patches.sh >/dev/null || exit 1
export PYTHONPATH="$PWD/tinygrad_repo:$PWD"
MODE=${1:-beam}
LOG=/root/s20logs/slow_$MODE.log; mkdir -p /root/s20logs
BEAMENV=""; [ "$MODE" = "beam" ] && BEAMENV="BEAM=2 S20_BEAM_CACHE_ONLY=1"
echo "== $MODE: compiling and timing every kernel (a few minutes), log $LOG"
env $BEAMENV DEBUG=2 S20_WAIT_MAX_S=60 S20_REG_LIMIT=64 DEV=QCOM:IR3 IMAGE=0 FLOAT16=1 NOLOCALS=1 JIT_BATCH_SIZE=0 \
  OPENPILOT_HACKS=1 python tinygrad_repo/examples/openpilot/compile_onnx.py \
  openpilot/selfdrive/modeld/models/driving_supercombo.onnx /tmp/s20_slow.pkl --out-of-band --benchmark-runs 3 > $LOG 2>&1
echo "== rc=$?  whole model:"
grep -E "^\s*\[[0-9]+/3\]" $LOG | tail -3
python3 - "$LOG" <<'EOF'
import re, sys
from collections import defaultdict
unit = {"us": 1e-3, "ms": 1.0, "s": 1e3}
worst = {}  # kernel name -> (slowest time ms, kernel number)
for line in open(sys.argv[1], errors="replace"):
  line = re.sub(r"\x1b\[[0-9;]*[mK]", "", line)
  m = re.match(r"\*\*\* (\S+)\s+(\d+)\s+(\S+).*? tm\s+([\d.]+)(us|ms|s)\s*/", line)
  if m and "copy" not in m.group(3):
    t = float(m.group(4)) * unit[m.group(5)]
    if t > worst.get(m.group(3), (-1, 0))[0]: worst[m.group(3)] = (t, int(m.group(2)))
if not worst:
  print("no kernel timings found (see the log)"); sys.exit()
print(f"== {len(worst)} different kernels timed. slowest:")
for name, (t, k) in sorted(worst.items(), key=lambda x: -x[1][0])[:12]:
  print(f"  {t:9.2f} ms  #{k:<5d} {name}")
EOF
echo "== GPU messages (kgsl):"
dmesg 2>/dev/null | grep -iE "kgsl|adreno" | tail -5
