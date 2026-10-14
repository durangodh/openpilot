package ai.comma.naverhud;

import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.lang.reflect.ParameterizedType;
import java.lang.reflect.Type;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * NaviStore 게터를 이름이 아니라 반환 타입(LiveData<…> 의 타입 인자)으로 찾는다.
 *
 * 게터 이름은 난독화라 업데이트마다 바뀐다(6.10.0.16 의 z0/o0/b0 → 6.11.1.2 의 E0/s0/c0).
 * 반환 타입의 클래스 이름은 난독화되지 않았고 두 버전에서 같았다. 타입으로 하나만 찾히지
 * 않으면 6.10 이름을 쓰되, 그 게터의 반환 타입이 맞을 때만 쓴다.
 */
final class NaverStore {
    /** 키 → 6.10.0.16 게터 이름(타입으로 못 찾을 때 쓴다). */
    private static final String[][] KEYS = {
            {"NaviState", "b0"},
            {"TbtData", "z0"},
            {"GuidanceRemaining", "o0"},
            {"GuidePoint", "P"},
            {"AddressAndRoadNameData", "B"},
            {"NaviLaneItem", "S"},
            {"TrafficSignalInfo", "B0"},
            {"CurrentRoute", "K"},
            {"List<GuidanceSafety>", "q0"},
            {"MatchedLinkInfo", "T"},
            // 단일 GuidanceSafety 게터 두 개(첫·둘째 안전 안내). 이름 순서로 구분한다:
            // 6.10 r0 < s0, 6.11 w0 < x0 (둘 다 같은 필드 순서였다).
            {"GuidanceSafety#0", "r0"},
            {"GuidanceSafety#1", "s0"},
    };

    private static final Map<String, Method> getters = new HashMap<>();
    private static Class<?> resolvedFor;

    private NaverStore() { }

    /** store 의 key 게터를 부른다(LiveData 면 LiveData 자체를 돌려준다). */
    static Object get(Object store, String key) {
        if (store == null) return null;
        Method m;
        synchronized (getters) {
            if (resolvedFor != store.getClass()) resolve(store.getClass());
            m = getters.get(key);
        }
        if (m == null) return null;
        try {
            return m.invoke(store);
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static void resolve(Class<?> type) {
        getters.clear();
        resolvedFor = type;
        Map<String, List<Method>> byKey = new HashMap<>();
        for (Method m : type.getDeclaredMethods()) {
            int mod = m.getModifiers();
            if (!Modifier.isPublic(mod) || Modifier.isStatic(mod) || m.getParameterTypes().length != 0) continue;
            String key = keyOf(m);
            if (key == null) continue;
            List<Method> list = byKey.get(key);
            if (list == null) byKey.put(key, list = new ArrayList<>());
            list.add(m);
        }
        StringBuilder names = new StringBuilder();
        for (String[] entry : KEYS) {
            String key = entry[0];
            Method chosen = null;
            if (key.startsWith("GuidanceSafety#")) {
                List<Method> list = byKey.get("GuidanceSafety");
                int index = key.charAt(key.length() - 1) - '0';
                if (list != null && list.size() == 2) {
                    list.sort(Comparator.comparing(Method::getName));
                    chosen = list.get(index);
                }
            } else {
                List<Method> list = byKey.get(key);
                if (list != null && list.size() == 1) chosen = list.get(0);
            }
            if (chosen == null) {
                // 6.10 이름도 반환 타입이 맞을 때만 쓴다(다른 버전에서는 같은 이름이 다른 게터다).
                Method old = byName(type, entry[1]);
                String want = key.startsWith("GuidanceSafety#") ? "GuidanceSafety" : key;
                if (old != null && want.equals(keyOf(old))) chosen = old;
            }
            if (chosen != null) {
                chosen.setAccessible(true);
                getters.put(key, chosen);
            }
            names.append(names.length() == 0 ? "" : " ").append(key).append('=')
                    .append(chosen == null ? "-" : chosen.getName());
        }
        NaverHudLog.line("NaviStore getters: " + names);
    }

    /** LiveData<X> / MutableLiveData<X> 이면 X 의 단순 이름(List<X> 는 "List<X>"), 그 밖에는 반환 클래스 이름. */
    private static String keyOf(Method m) {
        Type generic = m.getGenericReturnType();
        if (generic instanceof ParameterizedType) {
            ParameterizedType p = (ParameterizedType) generic;
            String raw = ((Class<?>) p.getRawType()).getSimpleName();
            if (!raw.endsWith("LiveData") || p.getActualTypeArguments().length != 1) return null;
            return simpleName(p.getActualTypeArguments()[0]);
        }
        Class<?> ret = m.getReturnType();
        return "MatchedLinkInfo".equals(ret.getSimpleName()) ? "MatchedLinkInfo" : null;
    }

    private static String simpleName(Type t) {
        if (t instanceof Class) return ((Class<?>) t).getSimpleName();
        if (t instanceof ParameterizedType) {
            ParameterizedType p = (ParameterizedType) t;
            Type[] args = p.getActualTypeArguments();
            if (args.length == 1 && List.class.equals(p.getRawType())) {
                return "List<" + simpleName(args[0]) + ">";
            }
        }
        return null;
    }

    private static Method byName(Class<?> type, String name) {
        try {
            return type.getMethod(name);
        } catch (Throwable ignored) {
            return null;
        }
    }
}
