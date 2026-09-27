package ai.comma.nmirrorpanel;

final class PlaybackMath {
  static long position(long base, long updated, float speed, boolean playing, long now, long duration) {
    double value = Math.max(0, base);
    if (playing && updated > 0 && now > updated && Float.isFinite(speed)) value += (now - updated) * (double) speed;
    long result = (long) Math.max(0, Math.min(Long.MAX_VALUE, value));
    return duration > 0 ? Math.min(duration, result) : result;
  }
  static int score(int state, boolean hasTitle) {
    int active = state == 3 ? 100 : state == 6 || state == 8 ? 80 : state == 2 ? 50 : 0;
    return active + (hasTitle ? 10 : 0);
  }
  static String time(long millis) {
    long seconds = Math.max(0, millis) / 1000;
    return seconds >= 3600 ? String.format(java.util.Locale.ROOT, "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60)
        : String.format(java.util.Locale.ROOT, "%d:%02d", seconds / 60, seconds % 60);
  }
}
