# CarrotNaver HUD5 text clarity fix

Status: signed with the recovered HUD4 key; all split certificates and alignment verified.

Release: https://github.com/durangodh/openpilot/releases/tag/carrot-naver-6.9.1.3-hud5

Install over HUD4 without uninstalling.

APKS SHA-256: `f043411a7d518588c90098a3a23a68f18469d3ae79f2ea071510e2f5f8d2a426`

Certificate SHA-256: `b8b4093f1351346ae1642299d0f75fad33b8764c004058b85b744d514c23e6ad`

- Preserve native map aspect ratio before cropping to the landscape HUD panel.
- Increase JPEG output from 640×384 / quality 65 to 960×576 / quality 90.
- Keep the source capture bounded to a 2048-pixel long edge.
- Existing native surface capture and non-map rejection remain in place.
- Crop removes top/bottom coverage for portrait inputs instead of flattening labels.

Validation: capture-routing and portrait/landscape geometry regression checks,
Android API 35 compilation, D8/Smali assembly, JPEG metadata/quality verification,
and byte-for-byte preservation of other APK payload entries passed.
Actual S9 text quality has not yet been verified.
