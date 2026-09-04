# S9 HUD full-frame vehicle detector

> Legacy reference only: the manager no longer starts this SNPE/DLC daemon.
> The supported implementation now bundles MobileNetV1 TFLite in the Android
> APK and runs inference on the S9 from a low-rate EON camera preview. No DLC or
> Qualcomm SDK installation is required.

`vehicle_detectord` is an optional, display-only detector for EON. It reads the
road RGB VisionIPC stream, runs a small network on the Qualcomm SNPE DSP, maps
the bottom centre of each vehicle box onto the calibrated road plane, and
atomically publishes `/dev/shm/vision_vehicle_objects.json` for `remote_hud`.
It never publishes cereal messages consumed by `radard`, `plannerd`, or
`controlsd`.

No model is bundled. A DLC must match both the old EON SNPE runtime and its DSP
quantisation requirements, and its redistribution licence must permit use.
Put the model at:

```
/data/openpilot/models/vehicle_detector.dlc
```

The default contract in `models/vehicle_detector.json` is:

- one float NHWC input `[1, 300, 300, 3]`, RGB, `(pixel - 128) / 128`;
- either outputs whose names contain `boxes`, `scores`, `classes`, and optional
  `num_detections`, or one Caffe SSD `DetectionOutput` tensor;
- normalised `ymin,xmin,ymax,xmax` boxes by default;
- one-based COCO vehicle IDs `2,3,4,6,8` (bicycle, car, motorcycle, bus, truck).

Edit the JSON only when the model contract differs. Zero-based COCO models,
different output names, NCHW inputs, or integer user buffers do not work with
the default configuration.

Enable `S9 EXTERNAL HUD`, then `S9 HUD ALL VEHICLES (DSP)` in settings. The
default is 2 FPS with a 55% confidence threshold. The daemon deliberately has
no CPU fallback, lowers its process priority, pauses at 82 C until 78 C, and
enters a cooldown when DSP inference repeatedly exceeds 250 ms. A missing or
incompatible model leaves the existing supercombo lead markers working and
sets `EonClusterHudVisionDetectorStatus` instead of affecting driving.

This expands visual coverage but is not physical radar: distance comes from a
flat-ground projection and will be less reliable on slopes, crests, dips,
partly hidden vehicles, and non-road objects. It must remain display-only.
