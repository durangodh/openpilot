package ai.comma.hudcommon;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.Collection;
import java.util.Locale;

/**
 * 후면 단속카메라 표시 찾기(티맵·카카오 공용). 네이버는 카메라 속성 목록에 RearCamera 가
 * 들어 있었다(2026-10-30 실차). 티맵·카카오는 아직 실차 기록이 없어, 안전운전 항목의
 * 필드에서 같은 종류의 표시를 찾는다:
 *  - 이름에 rear 가 들어간 필드가 true 이거나 0 이 아니면,
 *  - 값(enum 이름·문자열·목록 항목)에 Rear / REAR / 후면 / Backward 가 들어 있으면 후면 카메라.
 * 카카오는 코드 이름이 ...BackwardCamera 다(2026-11-02 19:46 실차:
 * KNSafetyCode_SignalAndSpeedViolationBackwardCamera, raw 103).
 */
public final class RearCamera {
    private RearCamera() {}

    public static boolean isRear(Object item) {
        return scan(item, 3);
    }

    /** sdi JSON 끝에 "rear":true 를 붙인다(json 이 null 이면 그대로). */
    public static String withRear(String json, boolean rear) {
        if (!rear || json == null || !json.endsWith("}")) return json;
        return json.substring(0, json.length() - 1) + ",\"rear\":true}";
    }

    private static boolean scan(Object value, int depth) {
        if (value == null || depth <= 0) return false;
        if (isRearText(value)) return true;
        if (value instanceof Collection) {
            try {
                for (Object item : (Collection<?>) value) {
                    if (scan(item, depth - 1)) return true;
                }
            } catch (Throwable ignored) {
                // 앱이 목록을 바꾸는 중이면 다음 읽기에서 다시 본다.
            }
            return false;
        }
        if (value instanceof Number || value instanceof Boolean || value instanceof CharSequence
                || value instanceof Enum || isPlatform(value.getClass())) {
            return false;
        }
        for (Class<?> type = value.getClass(); type != null && !isPlatform(type); type = type.getSuperclass()) {
            for (Field field : type.getDeclaredFields()) {
                if (Modifier.isStatic(field.getModifiers()) || field.isSynthetic()) continue;
                Object child;
                try {
                    field.setAccessible(true);
                    child = field.get(value);
                } catch (Throwable error) {
                    continue;
                }
                if (field.getName().toLowerCase(Locale.ROOT).contains("rear")) {
                    if (Boolean.TRUE.equals(child)) return true;
                    if (child instanceof Number && ((Number) child).longValue() != 0) return true;
                }
                if (scan(child, depth - 1)) return true;
            }
        }
        return false;
    }

    private static boolean isRearText(Object value) {
        String text;
        if (value instanceof Enum) text = ((Enum<?>) value).name();
        else if (value instanceof CharSequence) text = value.toString();
        else return false;
        return text.contains("Rear") || text.contains("REAR") || text.contains("후면")
                || text.contains("Backward") || text.contains("BACKWARD");
    }

    private static boolean isPlatform(Class<?> type) {
        String name = type.getName();
        return name.startsWith("java.") || name.startsWith("javax.") || name.startsWith("android.")
                || name.startsWith("kotlin.") || name.startsWith("kotlinx.") || name.startsWith("androidx.");
    }
}
