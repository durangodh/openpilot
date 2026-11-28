package s20cam;

import android.media.Image;
import android.os.SystemClock;

import java.io.File;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.MappedByteBuffer;
import java.nio.channels.FileChannel;
import java.util.Arrays;

/**
 * Frame ring in a shared file, read by s20camerad in the Ubuntu chroot (s20/camerad/ring.py has the same layout).
 * Little endian. Header (4096 bytes):
 *   0 u32 magic 'S20C'   4 u32 version=1   8 u32 width   12 u32 height   16 u32 frame_size (NV12, w*h*3/2)
 *  20 u32 slots          24 u64 latest frame id (+1, 0 = none yet)
 *  32 f32 focal length of the frame (px)   36 u32 flags (bit0: rotated 180)
 *  64 + 32*i  slot i: u64 seq (frame id + 1 when complete, 0 while writing)  u64 frame id
 *                     u64 sensor timestamp ns (Image.getTimestamp)  u64 written at ns (CLOCK_BOOTTIME)
 * Frames: NV12 (Y plane width*height, then interleaved U,V rows) at 4096 + i*frame_size.
 * The camera image (capW x capH) is placed at (offX, offY) inside the frame; the border stays black.
 */
final class RingWriter {
  static final int MAGIC = 0x43303253;  // "S20C"
  static final int SLOTS = 4;
  static final int HEADER = 4096;

  private final MappedByteBuffer buf;
  private final int width, height, frameSize, capW, capH, offX, offY;
  private final boolean flip;
  private final byte[] row, urow, vrow;
  private long frameId = 0;

  RingWriter(String path, int width, int height, int capW, int capH, int offX, int offY, float focal, boolean flip)
      throws Exception {
    this.width = width; this.height = height; this.capW = capW; this.capH = capH;
    this.flip = flip;
    // rotated 180 degrees: the image also moves to the mirrored position in the frame
    this.offX = (flip ? width - offX - capW : offX) & ~1;
    this.offY = (flip ? height - offY - capH : offY) & ~1;
    if (this.offX < 0 || this.offY < 0 || this.offX + capW > width || this.offY + capH > height)
      throw new IllegalArgumentException("capture " + capW + "x" + capH + " at " + offX + "," + offY + " does not fit " + width + "x" + height);
    this.frameSize = width * height * 3 / 2;
    this.row = new byte[capW];
    this.urow = new byte[2 * capW];
    this.vrow = new byte[2 * capW];
    File f = new File(path);
    RandomAccessFile raf = new RandomAccessFile(f, "rw");
    long size = HEADER + (long) SLOTS * frameSize;
    raf.setLength(size);
    buf = raf.getChannel().map(FileChannel.MapMode.READ_WRITE, 0, size);
    buf.order(ByteOrder.LITTLE_ENDIAN);
    buf.putInt(0, 0);
    for (int i = 0; i < SLOTS; i++) buf.putLong(64 + 32 * i, 0);
    // black frames: Y=16, U=V=128
    byte[] y = new byte[width * height], uv = new byte[width * height / 2];
    Arrays.fill(y, (byte) 16); Arrays.fill(uv, (byte) 128);
    for (int i = 0; i < SLOTS; i++) { put(HEADER + i * frameSize, y, y.length); put(HEADER + i * frameSize + y.length, uv, uv.length); }
    buf.putLong(24, 0);
    buf.putInt(4, 1);
    buf.putInt(8, width);
    buf.putInt(12, height);
    buf.putInt(16, frameSize);
    buf.putInt(20, SLOTS);
    buf.putFloat(32, focal);
    buf.putInt(36, flip ? 1 : 0);
    buf.putInt(0, MAGIC);
    f.setReadable(true, false);
  }

  long frames() { return frameId; }

  /** Writes the frame; returns its mean luma (0..255, sampled every 16 px) for the exposure loop. */
  int write(Image img) {
    long id = frameId++;
    int slot = (int) (id % SLOTS);
    int meta = 64 + 32 * slot;
    buf.putLong(meta, 0);  // writing
    int base = HEADER + slot * frameSize;
    Image.Plane[] p = img.getPlanes();
    ByteBuffer ys = p[0].getBuffer();
    int yrs = p[0].getRowStride(), yps = p[0].getPixelStride();
    long lumaSum = 0; int lumaN = 0;
    for (int y = 0; y < capH; y++) {
      int off = y * yrs;
      if (yps == 1) { ys.position(off); ys.get(row, 0, Math.min(capW, ys.capacity() - off)); }
      else for (int x = 0; x < capW; x++) row[x] = ys.get(off + x * yps);
      if ((y & 15) == 0) for (int x = 0; x < capW; x += 16) { lumaSum += row[x] & 0xff; lumaN++; }
      if (flip) reverse(row, capW, 1);
      int dy = flip ? capH - 1 - y : y;
      put(base + (offY + dy) * width + offX, row, capW);
    }
    // NV12: interleave U and V row by row (works for any pixel stride, NV21- or NV12-backed buffers alike)
    ByteBuffer u = p[1].getBuffer(), v = p[2].getBuffer();
    int cw = capW / 2, ch = capH / 2;
    int urs = p[1].getRowStride(), ups = p[1].getPixelStride(), vrs = p[2].getRowStride(), vps = p[2].getPixelStride();
    int uvBase = base + width * height;
    for (int y = 0; y < ch; y++) {
      int un = Math.min((cw - 1) * ups + 1, u.capacity() - y * urs), vn = Math.min((cw - 1) * vps + 1, v.capacity() - y * vrs);
      u.position(y * urs); u.get(urow, 0, un);
      v.position(y * vrs); v.get(vrow, 0, vn);
      for (int x = 0; x < cw; x++) { row[2 * x] = urow[x * ups]; row[2 * x + 1] = vrow[x * vps]; }
      if (flip) reverse(row, capW, 2);
      int dy = flip ? ch - 1 - y : y;
      put(uvBase + (offY / 2 + dy) * width + offX, row, capW);
    }
    buf.putLong(meta + 8, id);
    buf.putLong(meta + 16, img.getTimestamp());
    buf.putLong(meta + 24, SystemClock.elapsedRealtimeNanos());
    buf.putLong(meta, id + 1);
    buf.putLong(24, id + 1);
    return lumaN > 0 ? (int) (lumaSum / lumaN) : 0;
  }

  // reverse a row in units of `unit` bytes (1 for Y, 2 for UV pairs)
  private static void reverse(byte[] a, int n, int unit) {
    for (int i = 0, j = n - unit; i < j; i += unit, j -= unit) {
      for (int k = 0; k < unit; k++) { byte t = a[i + k]; a[i + k] = a[j + k]; a[j + k] = t; }
    }
  }

  private void put(int pos, byte[] data, int n) {
    buf.position(pos);
    buf.put(data, 0, n);
  }
}
