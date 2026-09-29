package android.util;

public final class Base64 {
    public static final int NO_WRAP = 2;

    private Base64() { }

    public static String encodeToString(byte[] bytes, int flags) {
        return java.util.Base64.getEncoder().encodeToString(bytes);
    }
}
