"""Reader for the frame rings written by the Android camera bridge (s20/android/src/s20cam/RingWriter.java)."""
import mmap, os, struct, time

MAGIC, HEADER = 0x43303253, 4096


class Ring:
  def __init__(self, path: str):
    self.f = open(path, "rb")
    self.mm = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
    magic, ver, self.width, self.height, self.frame_size, self.slots = struct.unpack_from("<6I", self.mm, 0)
    assert magic == MAGIC and ver == 1, f"{path}: not an s20cam ring (magic {magic:#x} version {ver})"

  def latest_id(self) -> int:
    return struct.unpack_from("<Q", self.mm, 24)[0] - 1  # -1: nothing yet

  def read(self, frame_id: int):
    """(frame_id, sensor_ts_ns, written_ns, nv12 bytes) for frame_id, or None if it was overwritten or torn."""
    slot = frame_id % self.slots
    meta = 64 + 32 * slot
    seq, fid, sensor_ts, written = struct.unpack_from("<4Q", self.mm, meta)
    if seq != frame_id + 1 or fid != frame_id: return None
    off = HEADER + slot * self.frame_size
    data = self.mm[off:off + self.frame_size]
    if struct.unpack_from("<Q", self.mm, meta)[0] != seq: return None  # rewritten while copying
    return fid, sensor_ts, written, data

  def wait_next(self, last_id: int, timeout: float = 1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
      fid = self.latest_id()
      if fid > last_id and (r := self.read(fid)) is not None: return r
      time.sleep(0.002)
    return None


def boottime_ns() -> int:
  return time.clock_gettime_ns(time.CLOCK_BOOTTIME)


def nv12_to_ppm(nv12: bytes, w: int, h: int, path: str):
  import numpy as np
  y = np.frombuffer(nv12, np.uint8, w * h).reshape(h, w).astype(np.float32)
  uv = np.frombuffer(nv12, np.uint8, w * h // 2, w * h).reshape(h // 2, w // 2, 2).astype(np.float32) - 128
  u = uv[..., 0].repeat(2, 0).repeat(2, 1); v = uv[..., 1].repeat(2, 0).repeat(2, 1)
  rgb = np.stack([y + 1.402 * v, y - 0.344 * u - 0.714 * v, y + 1.772 * u], -1).clip(0, 255).astype(np.uint8)
  with open(path, "wb") as f:
    f.write(f"P6 {w} {h} 255\n".encode()); f.write(rgb.tobytes())


if __name__ == "__main__":
  # python3 s20/camerad/ring.py /s20share/road.ring [seconds]: frame rate, latency, one snapshot
  import sys
  r = Ring(sys.argv[1])
  secs = float(sys.argv[2]) if len(sys.argv) > 2 else 5
  print(f"{sys.argv[1]}: {r.width}x{r.height}, {r.slots} slots")
  last, n, lat, gaps, t0 = r.latest_id(), 0, [], 0, time.monotonic()
  frame = None
  while time.monotonic() - t0 < secs:
    if (frame := r.wait_next(last)) is None: print("no frame for 1 s"); break
    gaps += frame[0] - last - 1 if last >= 0 else 0
    last = frame[0]; n += 1; lat.append((boottime_ns() - frame[2]) / 1e6)
  if n:
    print(f"{n / (time.monotonic() - t0):.1f} fps read, {gaps} frames skipped, bridge latency {sum(lat) / len(lat):.1f} ms avg, {max(lat):.1f} max")
    out = os.path.splitext(sys.argv[1])[0] + ".ppm"
    nv12_to_ppm(frame[3], r.width, r.height, out); print("snapshot:", out)
