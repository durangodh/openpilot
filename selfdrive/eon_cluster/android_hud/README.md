# Android remote HUD (experimental)

This optional companion moves the 1920x462 HUD render, JPEG compression and
TURZX `1cbe:0092` USB upload from the EON to an Android phone.  The EON sends a
small UDP JSON telemetry packet at 10 Hz. The already-compressed TMAP JPEG
received from the existing phone sender is forwarded unchanged over TCP; EON
does not decode, resize, composite, or re-encode it.

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

Keep both devices on the same hotspot/Wi-Fi network. UDP port 7210 and TCP port
7211 must be reachable. To return instantly to the existing direct EON HUD, set
`EonClusterHudRemote` false and `EonClusterHud` true.

## Rooted Galaxy S9 TMAP sender

Build and install the `app` with Android Studio (minSdk 26; rooted Galaxy S9
running LineageOS 20 / Android 13 is the target). Root access is not
required, so it does not interfere with the existing Carrot/TMAP sender setup.
Connect the TURZX panel through a powered USB-C OTG adapter. Select **EON Remote
HUD** in E-Mirror's auto-launch list. The app starts its foreground service and
closes its activity immediately; it never requests screen-capture permission.
It receives the original TMAP JPEG back from EON, draws the driving and system
panels, encodes JPEG quality 55 at 8 FPS, and uploads it to the panel. Version
0.2 renders the perspective road, model path surface, lane lines, road edges,
two radar leads, a shaded 3D-style ego/lead vehicle, turn signals and BSD on the
Galaxy S9. The EON only publishes compact scene coordinates and does no HUD
rendering. Version 0.3 keeps the existing road-surface colors while bending the
road to the model edges, draws perspective-scaled dashed lane markings, rotates
PNG vehicle sprites with the path, and replaces the old BSD dot/triangle with a
rear-quarter vehicle sprite. JPEG writes are split into 16 KiB USB chunks for
older Galaxy S9 Android builds.

USB access may require one approval after initial installation. Selecting the
app as the default handler for `1cbe:0092` lets Android grant access and launch
it automatically on later USB attachments/reboots.

The first version intentionally keeps the original EON USB HUD unchanged and
does not enable remote mode automatically. Vehicle control and CAN messages
are never accepted from the phone.
