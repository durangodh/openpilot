# Android remote HUD (experimental)

This optional companion moves the 1920x462 HUD render, JPEG compression and
TURZX `1cbe:0092` USB upload from the EON to an Android phone.  The EON sends a
small UDP JSON telemetry packet at 10 Hz; no camera or map pixels leave it.

## EON

The manager starts `remote_hud` persistently, but it sleeps unless enabled:

```sh
python - <<'PY'
from common.params import Params
p = Params()
p.put_bool("EonClusterHud", False)
p.put_bool("EonClusterHudRemote", True)
PY
```

Keep both devices on the same hotspot/Wi-Fi network. UDP port 7210 must be
reachable. To return instantly to the existing direct EON HUD, set
`EonClusterHudRemote` false and `EonClusterHud` true.

## Rooted Galaxy S9 TMAP sender

Build and install the `app` with Android Studio (minSdk 26; rooted Galaxy S9
running LineageOS 20 / Android 13 is the target). Root access is not
required by the first version, so it does not interfere with the existing
Carrot/TMAP sender setup. Connect the TURZX
panel through a powered USB-C OTG adapter. Open **EON Remote HUD**, tap start,
approve screen capture and USB access, then bring TMAP to the foreground in
landscape orientation. The app captures a reduced 960x540 TMAP frame, draws
the driving and system panels, encodes JPEG quality 55 at 8 FPS, and uploads it
to the panel. JPEG writes are split into 16 KiB USB chunks for older Galaxy S9
Android builds.

The first version intentionally keeps the original EON USB HUD unchanged and
does not enable remote mode automatically. Vehicle control and CAN messages
are never accepted from the phone.
