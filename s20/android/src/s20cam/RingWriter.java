package s20cam;

import android.media.Image;
import android.os.SystemClock;

import java.io.File;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.MappedByteBuffer;
import java.nio.channels.FileChannel;

/**
 * Frame ring in a shared file, read by s20camerad in the Ubuntu chroot (s20/camerad/ring.py has the same layout).
 * Little endian. Header (4096 bytes):
 *   0 u32 magic 'S20C'   4 u32 version=1   8 u32 width   12 u32 height   16 u32 frame_size (NV12, w*h*3/2)
 *  20 u32 slots          24 u64 latest frame id (+1, 0 = none yet)
 *  64 + 32*i  slot i: u64 seq (frame id + 1 when complete, 0 while writing)  u64 frame id
 *                     u64 sensor timestamp ns (Image.getTimestamp)  u64 written at ns (CLOCK_BOOTTIME)
 * Frames: NV12 (Y plane width*height, then interleaved U,V rows) at 4096 + i*frame_size.
 */
final class RingWriter {
  static final int MAGIC = 0x43303253;  // "S20C"
  static final int SLOTS = 4;
  static final int HEADER = 4096;

  private final MappedByteBuffer buf;
  private final int width, height, frameSize;
  private final byte[] row, urow, vrow;
  private long frameId = 0;

  RingWriter(String path, int width, int height) throws Exception {
    this.width = width;
    this.height = height;
    this.frameSize = width * height * 3 / 2;
    this.row = new byte[width];
    this.urow = new byte[2 * width];
    this.vrow = new byte[2 * width];
    File f = new File(path);
    RandomAccessFile raf = new RandomAccessFile(f, "rw");
    long size = HEADER + (long) SLOTS * frameSize;
    raf.setLength(size);
    buf = raf.getChannel().map(FileChannel.MapMode.READ_WRITE, 0, size);
    buf.order(ByteOrder.LITTLE_ENDIAN);
    for (int i = 0; i < SLOTS; i++) buf.putLong(64 + 32 * i, 0);
    buf.putLong(24, 0);
    buf.putInt(4, 1);
    buf.putInt(8, width);
    buf.putInt(12, height);
    buf.putInt(16, frameSize);
    buf.putInt(20, SLOTS);
    buf.putInt(0, MAGIC);
    f.setReadable(true, false);
  }

  long frames() { return frameId; }

  void write(Image img) {
    long id = frameId++;
    int slot = (int) (id % SLOTS);
    int meta = 64 + 32 * slot;
    buf.putLong(meta, 0);  // writing
    int base = HEADER + slot * frameSize;
    Image.Plane[] p = img.getPlanes();
    copyPlane(p[0].getBuffer(), p[0].getRowStride(), p[0].getPixelStride(), base, width, height);
    // NV12: interleave U and V row by row (works for any pixel stride, NV21- or NV12-backed buffers alike)
    ByteBuffer u = p[1].getBuffer(), v = p[2].getBuffer();
    int cw = width / 2, ch = height / 2;
    int urs = p[1].getRowStride(), ups = p[1].getPixelStride(), vrs = p[2].getRowStride(), vps = p[2].getPixelStride();
    int uvBase = base + width * height;
    for (int y = 0; y < ch; y++) {
      int un = Math.min((cw - 1) * ups + 1, u.capacity() - y * urs), vn = Math.min((cw - 1) * vps + 1, v.capacity() - y * vrs);
      u.position(y * urs); u.get(urow, 0, un);
      v.position(y * vrs); v.get(vrow, 0, vn);
      for (int x = 0; x < cw; x++) { row[2 * x] = urow[x * ups]; row[2 * x + 1] = vrow[x * vps]; }
      put(uvBase + y * width, row, width);
    }
    buf.putLong(meta + 8, id);
    buf.putLong(meta + 16, img.getTimestamp());
    buf.putLong(meta + 24, SystemClock.elapsedRealtimeNanos());
    buf.putLong(meta, id + 1);
    buf.putLong(24, id + 1);
  }

  private void copyPlane(ByteBuffer src, int rowStride, int pixelStride, int dst, int w, int h) {
    for (int y = 0; y < h; y++) {
      int off = y * rowStride;
      if (pixelStride == 1) {
        int n = Math.min(w, src.capacity() - off);
        src.position(off);
        src.get(row, 0, n);
        put(dst + y * width, row, n);
      } else {
        for (int x = 0; x < w; x++) row[x] = src.get(off + x * pixelStride);
        put(dst + y * width, row, w);
      }
    }
  }

  private void put(int pos, byte[] data, int n) {
    buf.position(pos);
    buf.put(data, 0, n);
  }
}
