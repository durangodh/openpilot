package ai.comma.eonlink;

import java.io.*;
import java.net.*;
import java.nio.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

/** Single EON, bounded in-flight frames; native engine stays unchanged. */
public final class Relay implements Closeable {
  public interface Events { void status(String text); }
  public static final int PORT = 5600, DISCOVERY = 5601, ENGINE = 5599;
  public static final String SHA = "01effc679eb46d6036ce0302673a87fe2dcfcc95a679daeeb929679e53c3647a";
  private volatile boolean running = true;
  private ServerSocket listener;
  private volatile Socket client, nativeSocket;
  private DatagramSocket discovery;
  private final AtomicBoolean occupied = new AtomicBoolean();
  private final Events events;
  private final int enginePort;
  private static final class Timing {
    final int receiveUs, inflateUs; final long sent;
    Timing(Wire.Packet p) { receiveUs = p.receiveUs; inflateUs = p.inflateUs; sent = System.nanoTime(); }
  }
  public Relay(Events events, int enginePort) { this.events = events; this.enginePort = enginePort; }
  public int start(int port) throws IOException {
    listener = new ServerSocket(); listener.setReuseAddress(true); listener.bind(new InetSocketAddress(port));
    new Thread(this::accept, "eon-accept").start();
    return listener.getLocalPort();
  }
  public boolean hasClient() { return occupied.get(); }
  private void accept() {
    while (running) {
      try {
        Socket s = listener.accept();
        if (!s.getInetAddress().isSiteLocalAddress() && !s.getInetAddress().isLoopbackAddress()) { s.close(); continue; }
        if (!occupied.compareAndSet(false, true)) { s.close(); continue; }
        client = s;
        new Thread(() -> serve(s), "eon-session").start();
      } catch (IOException e) { if (running) events.status("연결 오류: " + e.getMessage()); }
    }
  }
  private void serve(Socket s) {
    final ConcurrentHashMap<Integer, Timing> times = new ConcurrentHashMap<>();
    Socket engine = null;
    try {
      s.setTcpNoDelay(true); s.setSoTimeout(5000); s.setKeepAlive(true);
      // Echo tests never open the model socket or change recurrent state.
      InputStream input = s.getInputStream(); OutputStream output = s.getOutputStream();
      Wire.Packet first;
      while (true) {
        first = Wire.read(input);
        if ((first.flags & Wire.ECHO) == 0) break;
        synchronized (output) { first.data = java.security.MessageDigest.getInstance("SHA-256").digest(first.data); Wire.write(output, first); }
        events.status("전송 시험 연결됨");
      }
      engine = new Socket(); engine.connect(new InetSocketAddress("127.0.0.1", enginePort), 3000);
      engine.setTcpNoDelay(true); engine.setKeepAlive(true); engine.setSoTimeout(65000);
      nativeSocket = engine;
      final Socket backend = engine;
      s.setSoTimeout(65000);
      Thread replies = new Thread(() -> {
        try {
          while (running && !s.isClosed()) {
            byte[] wire = Wire.readNative(backend.getInputStream());
            int seq = Wire.sequence(wire); Wire.Packet p = new Wire.Packet(0, seq, wire);
            if (Wire.kind(wire) == 9) {
              Timing t = times.remove(seq);
              if (t == null) throw new IOException("unmatched inference reply");
              p.receiveUs = t.receiveUs; p.inflateUs = t.inflateUs; p.engineUs = Wire.micros(t.sent);
              events.status("N16 응답 · 폰 수신 " + p.receiveUs / 1000.0 + "ms · 엔진 왕복 " + p.engineUs / 1000.0 + "ms");
            }
            synchronized (output) { Wire.write(output, p); }
          }
        } catch (IOException e) { closeSocket(s); closeSocket(backend); }
      }, "eon-replies");
      replies.start();
      Wire.Packet p = first;
      int previous = 0;
      while (running && !s.isClosed()) {
        int seq = Wire.sequence(p.data), kind = Wire.kind(p.data);
        if (p.flags != 0 || seq != p.sequence || seq <= previous || (kind != 1 && kind != 3 && kind != 8))
          throw new IOException("EON request order/type");
        previous = seq;
        if (kind == 8) {
          if (!times.isEmpty()) throw new IOException("only one inference in flight");
          times.put(seq, new Timing(p));
        }
        backend.getOutputStream().write(p.data); backend.getOutputStream().flush();
        p = Wire.read(input);
      }
    } catch (IOException | java.security.NoSuchAlgorithmException e) {
      if (running) events.status("연결 대기 · " + e.getMessage());
    } finally {
      closeSocket(s); closeSocket(engine); nativeSocket = null; client = null; occupied.set(false);
    }
  }
  public void startDiscovery() throws SocketException {
    discovery = new DatagramSocket(null); discovery.setReuseAddress(true);
    discovery.bind(new InetSocketAddress(DISCOVERY));
    new Thread(() -> {
      byte[] buffer = new byte[256];
      while (running) {
        try {
          DatagramPacket p = new DatagramPacket(buffer, buffer.length); discovery.receive(p);
          if (!p.getAddress().isSiteLocalAddress() && !p.getAddress().isLoopbackAddress()) continue;
          String text = new String(p.getData(), 0, p.getLength(), java.nio.charset.StandardCharsets.US_ASCII);
          if (!text.equals("EONLINK_DISCOVER_V1")) continue;
          byte[] reply = ("{\"app\":\"eonlink\",\"version\":1,\"port\":5600,\"model\":\"" + SHA + "\"}")
            .getBytes(java.nio.charset.StandardCharsets.US_ASCII);
          discovery.send(new DatagramPacket(reply, reply.length, p.getAddress(), p.getPort()));
        } catch (IOException e) { if (running) events.status("자동 검색 오류"); }
      }
    }, "eon-discovery").start();
  }
  private static void closeSocket(Socket s) { if (s != null) try { s.close(); } catch (IOException ignored) {} }
  public void close() {
    running = false;
    try { if (listener != null) listener.close(); } catch (IOException ignored) {}
    if (discovery != null) discovery.close(); closeSocket(client); closeSocket(nativeSocket);
  }
}
