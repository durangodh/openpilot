# HUD remote: Bluetooth media remote → S9 Remote HUD → EON / turn-signal relay

Branch `g_remote`. Lets a cheap Bluetooth media remote (6–8 keys, mounted on
the wheel) operate openpilot cruise buttons and pulse the turn-signal stalk
without touching the EON or the car wiring beyond one relay.

```
BT remote ──(media keys)──▶ Remote HUD app ──HUDCMD1 (UDP 7210)──▶ EON remote_hud
                                  │                                   └─▶ /dev/shm/hud_remote_cmd.json
                                  │                                        └─▶ Hyundai car interface: ButtonEvent press/release
                                  └──(USB HID, OTG hub)──▶ 2ch relay ‖ stalk contacts ──▶ car turn signal ──▶ openpilot lane change
```

## Key map (RemoteInput.java, easy to change)

| key | short press | long press (≥ 600 ms) |
|---|---|---|
| VOL+ | RES / set speed + (`res`) | — |
| VOL− | SET / set speed − (`set`) | — |
| PLAY/PAUSE | cruise gap step (`gap`) | cruise CANCEL (`cancel`) |
| NEXT | navigation app toggle (`nav_toggle`) | right turn signal (relay 2) |
| PREV | — | left turn signal (relay 1) |

Volume keys have no press duration on Android, so they are short-only.
The HUD shows `리모컨 VOL+ → res` for two seconds in the drive panel on each key.

## EON side

* `selfdrive/eon_cluster/hud_remote.py`
  * `RemoteCommandSync` — inside `remote_hud`'s 7210 loop. Accepts idempotent
    `HUDCMD1 <session> <id> <cmd>`, acks via telemetry `hudCmdAck`, writes
    button commands to `/dev/shm/hud_remote_cmd.json` (`seq`, `cmd`, `ts`),
    applies `nav_toggle` straight to `EonClusterHudNavApp`.
  * `RemoteButtonSource` — inside the Hyundai car interface. Polls the file
    every 5 frames (`os.stat`), emits one `pressed=True` ButtonEvent for the
    mapped type (`accelCruise` / `decelCruise` / `cancel` / `gapAdjustCruise`)
    and the release on the next frame. Commands older than 1.5 s are dropped.
* `selfdrive/car/hyundai/interface.py` appends those events to
  `ret.buttonEvents`, so controlsd's set-speed logic, the engage path
  (`buttonEnable` on RES/SET release) and cruise_helper's gap cycling treat
  them exactly like wheel buttons. Only button presses can be injected; there
  is no steering or acceleration command anywhere in this path.

CPU: nothing periodic beyond a 20 Hz `stat()` of one tmpfs file.

## S9 side

* `RemoteInput.java` — `MediaSession` kept in PLAYING state so media keys
  arrive (`onMediaButtonEvent`), `VolumeProvider` for the volume keys,
  long-press detection on ACTION_DOWN/UP, request/ack/resend for commands,
  and a dcttech-style USB HID relay driver (VID `16C0` / PID `05DF`, HID
  SET_REPORT `[0xFF, ch]` on / `[0xFD, ch]` off, 300 ms pulse on a worker
  thread). USB permission is requested once; accept the dialog on the phone.
* `HudService.java` — creates/stops `RemoteInput`, remembers the EON reply
  address from the telemetry socket for `sendToEon`, feeds `hudCmdSession` /
  `hudCmdAck` from telemetry, draws the last remote event.

## Wiring the relay (installer)

Relay contacts are dry (COM/NO). Relay 1 NO → left signal line, Relay 2 NO →
right signal line, both COM → the stalk's common (usually chassis ground);
i.e. the same two points the stalk contact closes. NC unused. Verify with a
meter: the signal line sits at a voltage and drops to 0 V when the stalk is
moved. The relay board is powered from USB 5 V through the OTG hub.

## Test

```sh
PYTHONPATH=. python selfdrive/eon_cluster/test_hud_remote.py
```


## Lane change request (EON side, carrot "LANECHANGE")

`lane_left` / `lane_right` commands write `/dev/shm/hud_remote_lane.json`
(`direction`, `ts`). `RemoteLaneChangeSource` (in `desire_helper`, model
rate) returns the direction for 0.3 s after each command, so a sender that
repeats the command while the key is held produces a request that ends when
the key is released. The request is merged into the NOO virtual-blinker path
and therefore obeys every NOO gate: `LaneChangeNeedTorque` (-1 off / 0 auto /
1 driver nudge required), minimum speed, road edge, blind spot, opposite
torque, brake. No car lamp is switched on — same as carrot ATC/LANECHANGE.
The HUD-app key binding for this is not shipped yet.
