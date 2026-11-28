#!/bin/bash
# Stage 5a: compile comma's driving + driver-monitoring models for the S20 FE GPU and time them.
# Run inside the chroot, from the openpilot checkout root.
#   bash s20/tests/stage5_model.sh            # both models
#   bash s20/tests/stage5_model.sh driving    # driving model only
#
# Same tinygrad flags as comma hardware (selfdrive/modeld/SConscript, comma_arm64), except the
# renderer: DEV=QCOM:IR3 (Mesa) because tinygrad's QCOMCL compiler only targets the a630.
# Outputs go where modeld loads them: openpilot/selfdrive/modeld/models/*_tinygrad.pkl

cd "$(dirname "$0")/../.." || exit 1
ROOT=$PWD
MODELS=$ROOT/openpilot/selfdrive/modeld/models
export PYTHONPATH="$ROOT/tinygrad_repo:$ROOT:$PYTHONPATH"

echo "== packages"
command -v git-lfs >/dev/null || apt install -y git-lfs || exit 1
python3 -c "import numpy" 2>/dev/null || pip3 install --break-system-packages numpy || exit 1
python3 -c "import tinymesa" 2>/dev/null || pip3 install --break-system-packages tinymesa==25.2.7.2 || exit 1

echo "== s20 patches"
bash s20/apply_patches.sh || exit 1

echo "== model files (git lfs)"
git lfs install --local >/dev/null
git lfs pull --include="openpilot/selfdrive/modeld/models/*.onnx" || { echo "FAIL: git lfs pull"; exit 1; }
for f in driving_supercombo.onnx dmonitoring_model.onnx; do
  head -c 40 "$MODELS/$f" | grep -q "git-lfs" && { echo "FAIL: $f is still an LFS pointer"; exit 1; }
  ls -l "$MODELS/$f"
done

FLAGS="S20_REG_LIMIT=${S20_REG_LIMIT:-64} DEV=QCOM:IR3 FLOAT16=1 NOLOCALS=1 JIT_BATCH_SIZE=0 OPENPILOT_HACKS=1"

compile() {  # compile <onnx> <pkl>
  # IMAGE=1 runs on the a650 but its kernels spill registers and fall back to NOOPT (~100 ms vs ~55 ms), so
  # IMAGE=0 is the default; S20_IMAGE="1 0" tries IMAGE=1 first
  for image in ${S20_IMAGE:-0}; do
    echo "== $1 (IMAGE=$image)"
    t0=$(date +%s)
    if env $FLAGS IMAGE=$image python3 "$ROOT/tinygrad_repo/examples/openpilot/compile_onnx.py" \
         "$MODELS/$1" "$MODELS/$2" --out-of-band --benchmark-runs 10; then
      echo "OK: $2 (IMAGE=$image, compile $(( $(date +%s) - t0 )) s)"
      echo "IMAGE=$image" > "$MODELS/$2.s20flags"
      return 0
    fi
    echo "-- IMAGE=$image failed"
  done
  echo "FAIL: $1"; return 1
}

rc=0
compile driving_supercombo.onnx driving_tinygrad.pkl || rc=1
if [ "$1" != "driving" ]; then
  compile dmonitoring_model.onnx dmonitoring_model_tinygrad.pkl || rc=1
fi
[ $rc = 0 ] && echo "PASS" || echo "FAIL"
exit $rc
