package ai.comma.naverhud;

import java.lang.reflect.Field;
import java.lang.reflect.Modifier;

/** 네이버 안내 객체를 타입으로 찾는 공용 리플렉션 도우미. */
final class NaverReflect {
    static final String NAVI_UI = "com.naver.maps.navi.ui.map.NaverNaviUI";
    static final String NAVER_MAP = "com.naver.maps.map.NaverMap";

    private NaverReflect() {
    }

    /** 선언 타입이 typeName 인 첫 필드 값(상위 클래스 포함). */
    static Object fieldOfType(Object target, String typeName) {
        if (target == null) return null;
        for (Class<?> t = target.getClass(); t != null; t = t.getSuperclass()) {
            for (Field f : t.getDeclaredFields()) {
                if (Modifier.isStatic(f.getModifiers()) || !assignable(f.getType(), typeName)) continue;
                try {
                    f.setAccessible(true);
                    Object v = f.get(target);
                    if (v != null) return v;
                } catch (Throwable ignored) {
                    // 다음 필드
                }
            }
        }
        return null;
    }

    private static boolean assignable(Class<?> type, String typeName) {
        for (Class<?> t = type; t != null; t = t.getSuperclass()) {
            if (t.getName().equals(typeName)) return true;
            for (Class<?> i : t.getInterfaces()) if (i.getName().equals(typeName)) return true;
        }
        return false;
    }
}
