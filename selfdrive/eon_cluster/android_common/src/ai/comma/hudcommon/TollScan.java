package ai.comma.hudcommon;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;
import java.util.Collection;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * 톨게이트 통행료 필드 찾기(네이버·티맵·카카오 공용).
 *
 * 순정 HUD 0x123 은 톨게이트 그림과 함께 요금(10원 단위)을 보낸다. 네비 앱이 요금을 어느
 * 필드에 두는지 아직 모르므로, 톨게이트 안내 중에 이름이 toll/fee/charge/fare/cost/price 인
 * 필드를 모두 로그에 남기고, 이름이 분명한 요금 필드(예: tollFee, nTollFee)만 값으로 쓴다.
 */
public final class TollScan {
    private static final Pattern CANDIDATE = Pattern.compile("toll|fee|charge|fare|cost|price|pay|money|won");
    private static final Pattern FEE = Pattern.compile("toll.*(fee|charge|fare|cost|price|money)|(fee|charge|fare)$|^(n|i|m)?(fee|charge|fare)");
    private static final int MAX_ITEMS = 5;
    private static final int MAX_CHARS = 4000;
    public static final int MIN_FEE_WON = 10;
    public static final int MAX_FEE_WON = 655340;

    private TollScan() {}

    /** 안내 글자가 톨게이트(요금소)인지. */
    public static boolean isTollText(String text) {
        return text != null && (text.contains("톨게이트") || text.contains("요금소"));
    }

    /** HUD 가 받는 범위(10원 단위, 10~655340원)의 요금이면 그 값, 아니면 -1. */
    public static int validFee(long won) {
        return won >= MIN_FEE_WON && won <= MAX_FEE_WON && won % 10 == 0 ? (int) won : -1;
    }

    /** 요금 후보 필드를 "경로=값" 으로 모은다(로그용). 없으면 빈 문자열. */
    public static String candidates(Object root, int depth) {
        StringBuilder out = new StringBuilder();
        walk(root, "", depth, out, null);
        if (out.length() > MAX_CHARS) {
            out.setLength(MAX_CHARS);
            out.append("...");
        }
        return out.toString();
    }

    /** 이름이 분명한 요금 필드의 값(원). 없거나 범위 밖이면 -1. */
    public static int feeWon(Object root, int depth) {
        long[] fee = {-1};
        walk(root, "", depth, null, fee);
        return fee[0] < 0 ? -1 : validFee(fee[0]);
    }

    /** guidance JSON 끝에 toll_fee_won 을 붙인다(요금이 없거나 json 이 null 이면 그대로). */
    public static String withFee(String json, int feeWon) {
        if (json == null || feeWon <= 0 || !json.endsWith("}")) return json;
        return json.substring(0, json.length() - 1) + ",\"toll_fee_won\":" + feeWon + "}";
    }

    /** guidance JSON 끝에 "tollgate":true 를 붙인다(json 이 null 이면 그대로). */
    public static String withFlag(String json) {
        if (json == null || !json.endsWith("}")) return json;
        return json.substring(0, json.length() - 1) + ",\"tollgate\":true}";
    }

    private static void walk(Object value, String path, int depth, StringBuilder out, long[] fee) {
        if (value == null || depth <= 0) return;
        if (out != null && out.length() > MAX_CHARS) return;
        if (fee != null && fee[0] >= 0) return;
        if (value instanceof Collection) {
            int n = 0;
            for (Object item : (Collection<?>) value) {
                if (n >= MAX_ITEMS) break;
                walk(item, path + "[" + n++ + "]", depth - 1, out, fee);
            }
            return;
        }
        if (value.getClass().isArray() && !value.getClass().getComponentType().isPrimitive()) {
            Object[] items = (Object[]) value;
            for (int n = 0; n < items.length && n < MAX_ITEMS; n++) {
                walk(items[n], path + "[" + n + "]", depth - 1, out, fee);
            }
            return;
        }
        if (isLeaf(value) || isPlatform(value.getClass())) return;
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
                String name = field.getName();
                String lower = name.toLowerCase(Locale.ROOT);
                String childPath = path.isEmpty() ? name : path + "." + name;
                boolean candidate = CANDIDATE.matcher(lower).find()
                        || (child != null && CANDIDATE.matcher(child.getClass().getSimpleName().toLowerCase(Locale.ROOT)).find());
                if (out != null && candidate) {
                    if (out.length() > 0) out.append(", ");
                    out.append(childPath).append('=').append(ObjectDump.describe(child, 2, 600));
                }
                if (fee != null && child instanceof Number && FEE.matcher(lower).find()) {
                    fee[0] = ((Number) child).longValue();
                    return;
                }
                if (child != null && !isLeaf(child)) walk(child, childPath, depth - 1, out, fee);
            }
        }
    }

    private static boolean isLeaf(Object value) {
        return value instanceof Number || value instanceof Boolean || value instanceof Character
                || value instanceof CharSequence || value instanceof Enum;
    }

    private static boolean isPlatform(Class<?> type) {
        String name = type.getName();
        return name.startsWith("java.") || name.startsWith("javax.") || name.startsWith("android.")
                || name.startsWith("kotlin.") || name.startsWith("kotlinx.") || name.startsWith("androidx.");
    }
}
