#!/usr/bin/env python3
"""
sensord for the Galaxy S20 FE: accelerometer/gyroscope from the Android bridge (s20cam, /s20share/imu.ring) published as
openpilot's `accelerometer` and `gyroscope` messages in the device frame (x forward, y right, z down; at rest the
accelerometer reads [0, 0, -9.81]).

The phone is mounted in landscape with the back camera looking forward, so forward is -Z of the Android sensor frame.
Which of Android X/Y points up depends on which way the phone is turned: it is found from gravity on the first second
of data (keep the phone still at start) and kept in /s20share/imu_mount.json. Fine tilt is left to calibrationd.
"""
import json, mmap, os, struct, time
import numpy as np

from openpilot.cereal import messaging, log

SHARE = os.getenv("S20_SHARE", "/s20share")
RING, MOUNT = os.path.join(SHARE, "imu.ring"), os.path.join(SHARE, "imu_mount.json")
MAGIC, HDR, REC = 0x49303253, 64, 32
TYPE_ACCEL, TYPE_GYRO, TYPE_GYRO_UNCAL = 1, 4, 16


class ImuRing:
  def __init__(self, path):
    self.f = open(path, "rb")
    self.mm = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
    magic, ver, rec, self.cap = struct.unpack_from("<4I", self.mm, 0)
    assert magic == MAGIC and ver == 1 and rec == REC, f"{path}: not an s20 imu ring"
    self.next = self.count()

  def count(self) -> int:
    return struct.unpack_from("<Q", self.mm, 16)[0]

  def read_new(self):
    """Yield (type, xyz, timestamp_ns) for records written since the last call; skip what was overwritten."""
    end = self.count()
    self.next = max(self.next, end - self.cap + 16)
    while self.next < end:
      off = HDR + (self.next % self.cap) * REC
      seq, typ, x, y, z, ts = struct.unpack_from("<QI3fq", self.mm, off)
      if seq == self.next + 1:
        yield typ, (x, y, z), ts
      self.next += 1


def mount_from_gravity(g: np.ndarray) -> np.ndarray:
  """Rotation (rows: device x fwd, y right, z down in Android coordinates) from mean accelerometer reading g."""
  axis = int(np.argmax(np.abs(g[:2])))           # X or Y carries gravity in landscape
  up = np.zeros(3); up[axis] = np.sign(g[axis])  # the specific force points up at rest
  fwd = np.array([0.0, 0.0, -1.0])               # back camera looks along -Z
  down = -up
  right = np.cross(down, fwd)
  return np.stack([fwd, right, down])


def load_or_find_mount(ring: ImuRing) -> np.ndarray:
  if os.path.exists(MOUNT) and not os.getenv("S20_IMU_RECAL"):
    with open(MOUNT) as f:
      return np.array(json.load(f)["R"])
  samples, t_end = [], time.monotonic() + 3
  while time.monotonic() < t_end and len(samples) < 100:
    samples += [v for typ, v, _ in ring.read_new() if typ == TYPE_ACCEL]
    time.sleep(0.01)
  if len(samples) < 20:
    raise SystemExit("s20sensord: no accelerometer data; is s20cam running?")
  g = np.mean(samples, axis=0)
  R = mount_from_gravity(g)
  with open(MOUNT, "w") as f:
    json.dump({"R": R.tolist(), "gravity_android": g.tolist()}, f)
  print(f"s20sensord: gravity {np.round(g, 2)} in Android frame -> mount saved to {MOUNT}")
  return R


def main():
  while not os.path.exists(RING):
    time.sleep(0.5)
  ring = ImuRing(RING)
  R = load_or_find_mount(ring)
  pm = messaging.PubMaster(["accelerometer", "gyroscope"])
  print("s20sensord: device frame rows (fwd, right, down) in Android axes:", R.tolist())
  while True:
    for typ, v, ts in ring.read_new():
      dev = (R @ np.asarray(v, dtype=np.float64)).tolist()
      if typ == TYPE_ACCEL:
        msg = messaging.new_message("accelerometer", valid=True)
        e = msg.accelerometer
        e.init("acceleration").v = dev
        service = "accelerometer"
      elif typ in (TYPE_GYRO, TYPE_GYRO_UNCAL):
        msg = messaging.new_message("gyroscope", valid=True)
        e = msg.gyroscope
        e.init("gyroUncalibrated").v = dev
        service = "gyroscope"
      else:
        continue
      e.timestamp = ts
      e.source = log.SensorEventData.SensorSource.android
      pm.send(service, msg)
    time.sleep(0.005)


if __name__ == "__main__":
  main()
