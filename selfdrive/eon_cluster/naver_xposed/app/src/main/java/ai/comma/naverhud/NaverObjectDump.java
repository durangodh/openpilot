package ai.comma.naverhud;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.Collection;

/**
 * 난독화된 네이버 객체의 모든 필드를 한 줄로 적는다(로그 분석용).
 * 후면 단속카메라처럼 코드값이 같아도 화면 표시가 다른 항목에서 구분값을 찾기 위해
 * 안전운전 항목(GuidanceSafety)의 필드 이름·값을 그대로 남긴다.
 */
final class NaverObjectDump {
    private static final int MAX_CHARS = 1500;
    private static final int MAX_TEXT = 60;
    private static final int MAX_ITEMS = 3;

    private NaverObjectDump() {}

    static String describe(Object value, int depth) {
        StringBuilder out = new StringBuilder();
        append(out, value, depth);
        if (out.length() > MAX_CHARS) {
            out.setLength(MAX_CHARS);
            out.append("...");
        }
        return out.toString();
    }

    private static void append(StringBuilder out, Object value, int depth) {
        if (out.length() > MAX_CHARS) return;
        if (value == null) {
            out.append("null");
        } else if (value instanceof Number || value instanceof Boolean || value instanceof Character) {
            out.append(value);
        } else if (value instanceof CharSequence) {
            String text = value.toString();
            if (text.length() > MAX_TEXT) text = text.substring(0, MAX_TEXT) + "...";
            out.append('"').append(text).append('"');
        } else if (value instanceof Enum) {
            out.append(((Enum<?>) value).name()).append('#').append(((Enum<?>) value).ordinal());
        } else if (value instanceof Collection) {
            Collection<?> items = (Collection<?>) value;
            out.append("list").append(items.size()).append('[');
            int n = 0;
            for (Object item : items) {
                if (n++ >= MAX_ITEMS) {
                    out.append(",...");
                    break;
                }
                if (n > 1) out.append(',');
                append(out, item, depth - 1);
            }
            out.append(']');
        } else if (depth <= 0 || isPlatform(value.getClass())) {
            out.append(value.getClass().getSimpleName());
        } else {
            appendFields(out, value, depth);
        }
    }

    private static void appendFields(StringBuilder out, Object value, int depth) {
        out.append(value.getClass().getName()).append('{');
        boolean first = true;
        for (Class<?> type = value.getClass(); type != null && !isPlatform(type); type = type.getSuperclass()) {
            for (Field field : type.getDeclaredFields()) {
                if (Modifier.isStatic(field.getModifiers()) || field.isSynthetic()) continue;
                if (!first) out.append(", ");
                first = false;
                out.append(field.getName()).append('=');
                try {
                    field.setAccessible(true);
                    append(out, field.get(value), depth - 1);
                } catch (Throwable error) {
                    out.append('?');
                }
                if (out.length() > MAX_CHARS) return;
            }
        }
        out.append('}');
    }

    private static boolean isPlatform(Class<?> type) {
        String name = type.getName();
        return name.startsWith("java.") || name.startsWith("javax.") || name.startsWith("android.")
                || name.startsWith("kotlin.") || name.startsWith("androidx.");
    }
}
