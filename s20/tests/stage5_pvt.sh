#!/bin/bash
# Stage 5: the driving model hangs the a650 on the first kernel that uses private memory (register spill).
# Try the private-memory register settings one combination at a time; report how far each gets.
#   bash s20/tests/stage5_pvt.sh
cd "$(dirname "$0")/../.." || exit 1
for perwave in auto 0; do
  for fibers in 4096 2048; do
    for shr in 11 12; do
      echo "### S20_PERWAVE=$perwave S20_PVT_FIBERS=$fibers S20_STACK_SHR=$shr"
      S20_PERWAVE=$perwave S20_PVT_FIBERS=$fibers S20_STACK_SHR=$shr bash s20/tests/stage5_debug.sh 0 \
        | grep -E "OK:|FAIL|finished" | cut -c1-120
    done
  done
done
