# CarrotNaver HUD5 text clarity fix

Status: source and unsigned APK built; signing pending HUD4 key recovery.

- Preserve native map aspect ratio before cropping to the landscape HUD panel.
- Increase JPEG output from 640×384 / quality 65 to 960×576 / quality 90.
- Keep the source capture bounded to a 2048-pixel long edge.
- Existing native surface capture and non-map rejection remain in place.
- Crop removes top/bottom coverage for portrait inputs instead of flattening labels.

Validation: capture-routing and portrait/landscape geometry regression checks,
Android API 35 compilation, D8/Smali assembly, JPEG metadata/quality verification,
and byte-for-byte preservation of other APK payload entries passed.
Actual S9 text quality has not yet been verified.
