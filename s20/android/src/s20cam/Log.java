package s20cam;

final class Log {
  private Log() {}
  static void i(String s) { System.out.println("[s20cam] " + s); System.out.flush(); }
  static void w(String s) { System.err.println("[s20cam] WARN " + s); System.err.flush(); }
}
