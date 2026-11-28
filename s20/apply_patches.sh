#!/bin/bash
# Apply the S20 patches to submodules. Safe to run more than once: the patched
# files are reset to the submodule's commit first, so an updated patch replaces an older one.
cd "$(dirname "$0")/.." || exit 1
for p in s20/patches/tinygrad_*.patch; do
  files=$(grep '^+++ b/' "$p" | sed 's|^+++ b/||')
  git -C tinygrad_repo checkout -- $files || { echo "FAIL: reset $files"; exit 1; }
  git -C tinygrad_repo apply "$PWD/$p" && echo "applied: $p" || { echo "FAIL: $p does not apply"; exit 1; }
done
