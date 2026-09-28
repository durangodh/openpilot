# CarrotNaver HUD13.14: bounded latest-map delivery

Build from the SHA-256 pinned, released HUD13.13 bundle, not the old HUD11-based
HUD14 workflow. Only snapshot helper classes in classes43.dex are replaced.
The SDK render-wake hook and 200 ms bridge cadence are verified in the input;
voice-button, marker-density, guidance and other APK payloads are preserved.

Changes:

- At most one map waits behind an in-progress send. A newer frame replaces and
  recycles the pending frame instead of adding another Handler job.
- Frames waiting more than 1200 ms are dropped; encoding also rechecks age,
  source generation and whether a newer frame arrived.
- Each snapshot request has a unique ticket and source generation. Timed-out,
  duplicate and old-source callbacks cannot unlock newer requests or enqueue maps.
- A null callback releases the request but is not counted as renderer recovery.
- Source replacement/removal invalidates pending maps and outstanding callbacks.

The in-progress socket write cannot be canceled by this queue policy. SDK
snapshot stalls and downstream network stalls can still cause visible pauses.
This is not a separate TMAP-style renderer, and on-device symptom resolution
has not been verified. The unrelated HUD14 build's missing smooth-map patches
are not evidence that the deployed HUD13.13 lacks those patches.

Tests use the production Java helper with fake Android/SDK dependencies:
50-frame backlog, in-flight sends, socket failure recovery, frame expiry,
late/duplicate/null callbacks, source changes, existing map selection and crop.
The release checks the assembled helper DEX and official signing certificate.
