"""Run the production encoder/queue methods against deterministic Java fakes."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "naver_xposed/app/src/main/java/ai/comma/naverhud/NaverMapCapture.java"


def method(source, signature):
  start = source.index(signature)
  opening = source.index("{", start)
  depth = 1
  end = opening + 1
  while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
  return source[start:end]


class JpegReuseTest(unittest.TestCase):
  def test_production_queue_and_encoder(self):
    source = SOURCE.read_text(encoding="utf-8")
    fragments = "\n".join(method(source, signature) for signature in (
      "private static final class Frame", "private void queueFrame(",
      "private void queueHudHeartbeat(", "private void drainEncode(", "private void encode("))
    fixture = r'''
import java.io.*;
import java.util.*;
import java.util.concurrent.atomic.*;
public class EncoderCheck {
  static final int JPEG_QUALITY = 65;
  static class Bitmap {
    enum CompressFormat { JPEG }
    static int compressions;
    static OutputStream buffer;
    final int value; boolean recycled, fail;
    Bitmap(int value) { this.value = value; }
    boolean compress(CompressFormat f, int quality, OutputStream out) throws IOException {
      check(!recycled, "use after recycle");
      check(quality == 65, "JPEG quality changed");
      if (fail) return false;
      if (buffer != null) check(buffer == out, "encoder buffer must be reused");
      buffer = out; compressions++; out.write(value); return true;
    }
    void recycle() { check(!recycled, "double recycle"); recycled = true; }
  }
  static class SystemClock { static long elapsedRealtime() { return 100; } }
  static class NaverHudLog {
    static void line(String s) {} static void ex(String s, Throwable t) { throw new AssertionError(t); }
  }
  static class Client {
    List<byte[]> frames = new ArrayList<>();
    void sendMap(byte[] bytes) { frames.add(bytes); }
  }
  static class Executor {
    List<Runnable> tasks = new ArrayList<>();
    void execute(Runnable r) { tasks.add(r); }
    void drain() { while (!tasks.isEmpty()) tasks.remove(0).run(); }
  }
  final Client client = new Client();
  final Executor encoder = new Executor();
  final AtomicReference<Frame> pendingFrame = new AtomicReference<>();
  final AtomicBoolean encodeScheduled = new AtomicBoolean();
  final ByteArrayOutputStream jpegBytes = new ByteArrayOutputStream(100000);
  byte[] lastHudJpeg;
  long generation, statReq, statOk, lastFrameAt, statDropped, statEncMsSum,
      statEncMsMax, statAgeMsMax, statSent, sent;
  static void check(boolean ok, String message) { if (!ok) throw new AssertionError(message); }
  public static void main(String[] args) {
    EncoderCheck c = new EncoderCheck();
    Bitmap first = new Bitmap(1);
    c.queueFrame(first, 0); c.encoder.drain();
    check(first.recycled, "encoded bitmap released");
    byte[] cached = c.client.frames.get(0);
    for (int i = 0; i < 10; i++) { c.queueHudHeartbeat(i); c.encoder.drain(); }
    check(Bitmap.compressions == 1, "heartbeat must not recompress");
    check(c.client.frames.get(10) == cached, "heartbeat must reuse exact JPEG");
    Bitmap next = new Bitmap(2);
    c.queueFrame(next, 10); c.queueHudHeartbeat(11);
    check(c.pendingFrame.get().image == next, "heartbeat replaced a fresh frame");
    c.encoder.drain();
    check(cached[0] == 1 && c.lastHudJpeg[0] == 2, "buffer reset mutated an in-flight JPEG");
    Bitmap dropped = new Bitmap(3), latest = new Bitmap(4);
    c.queueFrame(dropped, 12); c.queueFrame(latest, 13); c.encoder.drain();
    check(dropped.recycled && latest.recycled, "replaced and encoded images released");
    check(c.lastHudJpeg[0] == 4, "latest-frame behavior changed");
    Bitmap stale = new Bitmap(5);
    c.queueFrame(stale, 14); c.generation++; c.encoder.drain();
    check(stale.recycled && c.lastHudJpeg[0] == 4, "stale source updated cache");
    c.queueHudHeartbeat(15); c.encoder.drain();
    check(c.client.frames.get(c.client.frames.size()-1)[0] == 4, "rebuild lost last good HUD frame");
    Bitmap failed = new Bitmap(6); failed.fail = true;
    c.queueFrame(failed, 16); c.encoder.drain();
    check(failed.recycled && c.lastHudJpeg[0] == 4, "failed encoding corrupted cache");
    c.pendingFrame.set(new Frame(new Bitmap(7), c.generation, 17, false));
    c.drainEncode();
    check(c.lastHudJpeg[0] == 4, "phone snapshot replaced HUD rebuild cache");
    check(c.client.frames.get(c.client.frames.size()-1)[0] == 7, "fallback snapshot not sent");
    EncoderCheck empty = new EncoderCheck();
    empty.queueHudHeartbeat(0); empty.encoder.drain();
    check(empty.client.frames.isEmpty(), "heartbeat before first frame must be harmless");
    System.out.println("PASS JPEG reuse, buffer ownership, backpressure, generation, rebuild, failure, fallback");
  }
'''
    java = os.environ.get("JAVA_HOME")
    javac = str(Path(java) / "bin/javac") if java else "javac"
    runtime = str(Path(java) / "bin/java") if java else "java"
    with tempfile.TemporaryDirectory() as directory:
      path = Path(directory) / "EncoderCheck.java"
      path.write_text(fixture + fragments + "\n}", encoding="utf-8")
      subprocess.run([javac, "-d", directory, str(path)], check=True)
      subprocess.run([runtime, "-cp", directory, "EncoderCheck"], check=True)


if __name__ == "__main__":
  unittest.main()
