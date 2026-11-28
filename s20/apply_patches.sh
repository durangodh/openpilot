#!/bin/bash
# Apply the S20 patches to submodules. Safe to run more than once.
cd "$(dirname "$0")/.." || exit 1
for p in s20/patches/tinygrad_*.patch; do
  if git -C tinygrad_repo apply --reverse --check "$PWD/$p" 2>/dev/null; then
    echo "already applied: $p"
  elif git -C tinygrad_repo apply "$PWD/$p"; then
    echo "applied: $p"
  else
    echo "FAIL: $p does not apply (tinygrad_repo changed?)"; exit 1
  fi
done
