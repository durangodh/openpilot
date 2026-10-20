package ai.comma.eonbleremote;

import java.util.Locale;

/** Only a learned, exact address may activate the pairing restriction. */
public final class R1OnlyPolicy {
  private R1OnlyPolicy() { }

  public static String address(String selected) {
    if (selected == null) return "";
    String value = selected.toUpperCase(Locale.US);
    if (value.startsWith("BLE:")) value = value.substring(4);
    return value.matches("(?:[0-9A-F]{2}:){5}[0-9A-F]{2}") ? value : "";
  }

  public static boolean rejects(String selected, String candidate) {
    String allowed = address(selected);
    String other = address(candidate);
    return !allowed.isEmpty() && !other.isEmpty() && !allowed.equals(other);
  }
}
