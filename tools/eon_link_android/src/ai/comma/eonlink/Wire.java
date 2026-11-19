package ai.comma.eonlink;

import java.io.*;
import java.nio.*;
import java.util.zip.*;

/** EON1 framing, independent of Android for cross-language tests. All fields LE. */
public final class Wire {
  public static final int LIMIT = 1048576, HEADER = 32, DEFLATE = 1, ECHO = 2;
  public static final class Packet {
    public int flags, sequence, receiveUs, inflateUs, engineUs;
    public byte[] data;
    public Packet(int flags, int sequence, byte[] data) {
      this.flags = flags; this.sequence = sequence; this.data = data;
    }
  }
  public static byte[] readExact(InputStream in, int count) throws IOException {
    if (count < 0 || count > LIMIT) throw new IOException("packet size");
    byte[] data = new byte[count];
    int at = 0;
    while (at < count) {
      int n = in.read(data, at, count - at);
      if (n < 0) throw new EOFException();
      if (n == 0) continue;
      at += n;
    }
    return data;
  }
  public static Packet read(InputStream in) throws IOException {
    // Exclude idle time before the first byte from the phone's receive timer.
    int first = in.read();
    if (first < 0) throw new EOFException();
    long start = System.nanoTime();
    byte[] header = new byte[HEADER]; header[0] = (byte)first;
    byte[] rest = readExact(in, HEADER - 1); System.arraycopy(rest, 0, header, 1, rest.length);
    ByteBuffer b = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN);
    if (b.getInt() != 0x314e4f45 || b.get() != 1) throw new IOException("EON1 version");
    int flags = b.get() & 255;
    if ((flags & ~(DEFLATE | ECHO)) != 0 || b.getShort() != 0) throw new IOException("EON1 flags");
    int seq = b.getInt(), plain = b.getInt(), size = b.getInt();
    int rx = b.getInt(), unzip = b.getInt(), engine = b.getInt();
    if (plain < 0 || plain > LIMIT || size < 0 || size > LIMIT) throw new IOException("EON1 size");
    byte[] body = readExact(in, size);
    int received = micros(start);
    long inflateAt = System.nanoTime();
    if ((flags & DEFLATE) != 0) {
      Inflater inflater = new Inflater();
      try {
        inflater.setInput(body); byte[] output = new byte[plain]; int at = 0;
        while (at < plain && !inflater.finished()) {
          int n = inflater.inflate(output, at, plain - at);
          if (n == 0) throw new IOException("truncated EON1 deflate");
          at += n;
        }
        if (!inflater.finished() || at != plain || inflater.getRemaining() != 0) throw new IOException("EON1 deflate length");
        body = output;
      } catch (DataFormatException e) { throw new IOException("EON1 deflate", e); }
      finally { inflater.end(); }
    } else if (plain != size) throw new IOException("EON1 raw length");
    Packet p = new Packet(flags & ECHO, seq, body);
    // Incoming telemetry must be zero; replies are measured on this phone.
    if (rx != 0 || unzip != 0 || engine != 0) throw new IOException("request telemetry");
    p.receiveUs = received; p.inflateUs = micros(inflateAt);
    return p;
  }
  public static void write(OutputStream out, Packet p) throws IOException {
    if (p.data.length > LIMIT) throw new IOException("reply size");
    ByteBuffer b = ByteBuffer.allocate(HEADER).order(ByteOrder.LITTLE_ENDIAN);
    b.putInt(0x314e4f45).put((byte)1).put((byte)(p.flags & ECHO)).putShort((short)0)
      .putInt(p.sequence).putInt(p.data.length).putInt(p.data.length)
      .putInt(p.receiveUs).putInt(p.inflateUs).putInt(p.engineUs);
    // One write for small replies avoids a header-only segment/ACK dependency.
    byte[] whole = new byte[HEADER + p.data.length];
    System.arraycopy(b.array(), 0, whole, 0, HEADER);
    System.arraycopy(p.data, 0, whole, HEADER, p.data.length);
    out.write(whole); out.flush();
  }
  public static int micros(long start) {
    return (int)Math.min(Integer.MAX_VALUE, Math.max(0, (System.nanoTime() - start) / 1000));
  }
  public static int sequence(byte[] wire) throws IOException {
    ByteBuffer b = ByteBuffer.wrap(wire).order(ByteOrder.LITTLE_ENDIAN);
    if (wire.length < 32 || b.getInt(0) != 0x4b4e4c4a || b.getShort(4) != 3 || b.getLong(20) != 0 || b.getInt(28) != 0)
      throw new IOException("JLNK header");
    int flags = b.getInt(12), size = b.getInt(16);
    if ((flags & ~128) != 0 || size < 0 || size > LIMIT - 33 || wire.length != 32 + size + ((flags & 128) != 0 ? 1 : 0))
      throw new IOException("JLNK size");
    return b.getInt(8);
  }
  public static int kind(byte[] wire) { return ByteBuffer.wrap(wire).order(ByteOrder.LITTLE_ENDIAN).getShort(6) & 65535; }
  public static byte[] readNative(InputStream in) throws IOException {
    byte[] head = readExact(in, 32);
    ByteBuffer b = ByteBuffer.wrap(head).order(ByteOrder.LITTLE_ENDIAN);
    int size = b.getInt(16), flags = b.getInt(12);
    if (size < 0 || size > LIMIT - 33) throw new IOException("native size");
    byte[] tail = readExact(in, size + ((flags & 128) != 0 ? 1 : 0));
    byte[] all = new byte[32 + tail.length];
    System.arraycopy(head, 0, all, 0, 32); System.arraycopy(tail, 0, all, 32, tail.length);
    sequence(all); return all;
  }
}
