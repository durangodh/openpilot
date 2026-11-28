#!/bin/bash
# Apply the S20 patches to submodules: s20/patches/<name>_*.patch goes into <name>_repo (tinygrad_repo, msgq_repo).
# Safe to run more than once: the patched files are reset to the submodule's commit first.
cd "$(dirname "$0")/.." || exit 1
for p in s20/patches/*.patch; do
  sub="$(basename "$p" | cut -d_ -f1)_repo"
  files=$(grep '^+++ b/' "$p" | sed 's|^+++ b/||')
  git -C "$sub" checkout -- $files || { echo "FAIL: reset $sub: $files"; exit 1; }
  git -C "$sub" apply "$PWD/$p" && echo "applied: $p -> $sub" || { echo "FAIL: $p does not apply to $sub"; exit 1; }
done
