package io.zoompilot.jetlink.server;

/** JNI names preserved to reuse the pinned MIT Jetlink inference engine. */
public final class Native {
  static { System.loadLibrary("jetlink"); }
  public static native String start(String config);
  public static native void stop();
  public static native String command(String command);
  public static native String snapshot(long after, int timeoutMs);
  public static native String logs(long after);
  public static native void reportThermal(String label);
}
