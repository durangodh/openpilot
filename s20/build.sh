#!/bin/bash
# Stage 5b: build openpilot in the chroot for the S20 FE. Long (30-90 min). Log: /root/s20logs/build.log
#   cd /root/openpilot && bash s20/build.sh
set -o pipefail
cd "$(dirname "$0")/.." || exit 1
mkdir -p /root/s20logs
LOG=/root/s20logs/build.log
step() { echo "== $* ($(date +%H:%M:%S))" | tee -a $LOG; }
: > $LOG

step "submodules"
git submodule update --init --recursive --depth 1 2>&1 | tail -3 | tee -a $LOG || exit 1
step "git lfs"
command -v git-lfs >/dev/null || apt install -y git-lfs >>$LOG 2>&1
git lfs install --local >/dev/null; git lfs pull >>$LOG 2>&1 || { echo "FAIL: git lfs pull"; exit 1; }
step "dependencies (apt + uv)"
bash tools/setup_dependencies.sh >>$LOG 2>&1 || { echo "FAIL: setup_dependencies (see $LOG)"; tail -20 $LOG; exit 1; }
source .venv/bin/activate
uv pip install tinymesa==25.2.7.2 >>$LOG 2>&1 || pip install tinymesa==25.2.7.2 >>$LOG 2>&1
step "s20 patches"
bash s20/apply_patches.sh | tee -a $LOG || exit 1
step "scons (S20=1)"
S20=1 scons -j4 2>&1 | tee -a $LOG | grep -E "error|Error|\[ONNX\]|scons: done" | tail -40
rc=${PIPESTATUS[0]}
step "done rc=$rc"
[ $rc = 0 ] && echo "PASS: openpilot built" || { echo "FAIL: scons (last lines of $LOG):"; tail -30 $LOG; }
exit $rc
