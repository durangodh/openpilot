package com.naver.map.carrot;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.lang.reflect.Modifier;
import java.util.Locale;

/**
 * HUD8: map Naver NaviSDK codes onto the TMAP codes the EON / Remote HUD
 * already understand, so a Naver route drives NOO / ATC / speed control and
 * the HUD turn banner exactly like a TMAP route.
 *
 * Sources: CarrotNaver 6.9.1.3 {@code TurnPointType}, {@code SafetyCode} and
 * {@code LaneDirection} enums (classes8.dex).  Consumers of the TMAP codes:
 * EON {@code navigation_route.py} (TURN/FORK/UTURN/ROTARY sets),
 * {@code cruise_helper.py} (SDI type 22 = speed bump, any type with a limit
 * decelerates) and Remote HUD {@code turnDirection()} (12/13/14/2/16-19/20-22).
 */
public final class CarrotNaverCodes {
    // TMAP TBT codes used by this fork.
    static final int TBT_NONE = 0;
    static final int TBT_ARRIVE = 2;
    static final int TBT_STRAIGHT = 11;
    static final int TBT_LEFT = 12;
    static final int TBT_RIGHT = 13;
    static final int TBT_UTURN = 14;
    static final int TBT_LEFT_8 = 16;      // sharp-ish left, EON TURN_LEFT
    static final int TBT_LEFT_10 = 17;     // keep left, EON FORK_LEFT
    static final int TBT_RIGHT_2 = 18;     // keep right, EON FORK_RIGHT (after navigation_route fix)
    static final int TBT_RIGHT_4 = 19;     // sharp-ish right, EON TURN_RIGHT
    static final int TBT_LANE_LEFT = 20;   // HUD straight-left lane arrow
    static final int TBT_LANE_RIGHT = 21;  // HUD straight-right lane arrow
    static final int TBT_LANE_SPLIT = 22;
    static final int TBT_ROTARY_BASE = 131; // 131..142, EON ROTARY

    // TMAP SDI types used by this fork (cruise_helper only distinguishes 22).
    static final int SDI_SIGNAL_SPEED = 0;
    static final int SDI_SPEED = 1;
    static final int SDI_SECTION_START = 2;
    static final int SDI_SECTION_END = 3;
    static final int SDI_BUS = 4;
    static final int SDI_OTHER_CAM = 5;
    static final int SDI_SPEED_BUMP = 22;
    static final int SDI_INFO = 99;

    private CarrotNaverCodes() {
    }

    /** Naver TurnPointType.getValue() -> TMAP TBT code. */
    public static int turnType(int naver, String name) {
        String n = name == null ? "" : name;
        if (naver == 88 || n.contains("Goal")) {
            return TBT_ARRIVE;
        }
        switch (naver) {
            case 1:   // Straight
            case 111: // StraightAtTurn
            case 112: // LaneChangeStraight
            case 50: case 51: case 52: case 53: case 54: case 55: case 56: // *Straight access/exit
            case 75: case 78: // car-only straight
            case 83:  // DivideAndJoin
            case 85: case 86: case 87: // Rest, DrowsinessShelter, Via
            case 121: case 122: case 123: // tollgates
                return TBT_STRAIGHT;
            case 2:   // Left
            case 8:   // UnsafeLeft
            case 12:  // Direction9
                return TBT_LEFT;
            case 3:   // Right
            case 15:  // Direction3
                return TBT_RIGHT;
            case 6:   // UTurn
            case 7:   // PTurn (drawn as U-turn; NOO/ATC treat as uturn)
                return TBT_UTURN;
            case 11:  // Direction8 (8 o'clock)
                return TBT_LEFT_8;
            case 16:  // Direction4 (4 o'clock)
                return TBT_RIGHT_4;
            case 4:   // LeftDirection
            case 13:  // Direction11
            case 41:  // AccessLeft
            case 57: case 58: case 59: case 60: case 61: case 62: case 63: case 64: case 65: // *Left access/exit/side
            case 76: case 79: // car-only left
            case 81:  // JoinLeft
                return TBT_LEFT_10;
            case 5:   // RightDirection
            case 14:  // Direction1
            case 42:  // AccessRight
            case 66: case 67: case 68: case 69: case 70: case 71: case 72: case 73: case 74: // *Right access/exit/side
            case 77: case 80: // car-only right
            case 82:  // JoinRight
                return TBT_RIGHT_2;
            case 113: // LaneChangeLeftDirection
                return TBT_LANE_LEFT;
            case 114: // LaneChangeRightDirection
                return TBT_LANE_RIGHT;
            case 43: case 44: case 45: case 46: case 47: case 48: case 49: // tunnel/bridge/rest/ferry access
                return TBT_STRAIGHT;
            default:
                break;
        }
        if (naver >= 21 && naver <= 34) {          // Rotary*
            return rotary(naver - 21);
        }
        if (naver >= 91 && naver <= 104) {         // Roundabout*
            return rotary(naver - 91);
        }
        // Unknown numeric code: fall back to the enum name.
        String lower = n.toLowerCase(Locale.US);
        if (lower.contains("uturn")) {
            return TBT_UTURN;
        }
        if (lower.contains("left") && !lower.contains("right")) {
            return TBT_LEFT_10;
        }
        if (lower.contains("right") && !lower.contains("left")) {
            return TBT_RIGHT_2;
        }
        if (lower.contains("straight")) {
            return TBT_STRAIGHT;
        }
        return TBT_NONE;
    }

    /** index 0 = straight, 1 = uturn, 2.. = clock 7,8,9,10,11,12,1,2,3,4,5,6 -> 131..142. */
    private static int rotary(int index) {
        return TBT_ROTARY_BASE + Math.max(0, Math.min(11, index));
    }

    /** Naver SafetyCode value -> TMAP SDI type. */
    public static int sdiType(int naver, String name) {
        String n = name == null ? "" : name;
        if (naver == 104 || n.contains("SpeedBump")) {
            return SDI_SPEED_BUMP;
        }
        switch (naver) {
            case 1:   // SpeedCam
            case 6:   // BoxSpeedCam
            case 21:  // VariableSpeedCam
            case 23:  // MoveSpeedCam
            case 131: // SchoolZone (carries a limit)
            case 132: // SilverZone
                return SDI_SPEED;
            case 2:   // SpeedSignalCam
            case 22:  // VariableSpeedSignalCam
                return SDI_SIGNAL_SPEED;
            case 12:  // StartSectionSpeedCam
            case 14:  // VariableSectionStart
                return SDI_SECTION_START;
            case 13:  // EndSectionSpeedCam
            case 15:  // VariableSectionEnd
                return SDI_SECTION_END;
            case 4:   // BusCam
                return SDI_BUS;
            case 5: case 7: case 8: case 9: case 11: case 16: case 17: case 18: case 19: case 24: case 25:
                return SDI_OTHER_CAM;
            default:
                return SDI_INFO;
        }
    }

    /** Only speed-enforcing SDI types may carry a limit that the EON decelerates for. */
    public static boolean sdiLimitAllowed(int tmapType) {
        return tmapType == SDI_SPEED || tmapType == SDI_SIGNAL_SPEED
                || tmapType == SDI_SECTION_START || tmapType == SDI_SECTION_END;
    }

    /** Naver LaneDirection set (e.g. "[Forward, Left]") -> TMAP lane arrow code. */
    public static int laneTurn(String guideSet) {
        String v = guideSet == null ? "" : guideSet.toLowerCase(Locale.US);
        boolean back = v.contains("backward");
        boolean forward = v.contains("forward");
        boolean left = v.contains("left");
        boolean right = v.contains("right");
        if (back && !left && !right) {
            return TBT_UTURN;
        }
        if (forward && left && right) {
            return TBT_LANE_SPLIT;
        }
        if (forward && left) {
            return TBT_LANE_LEFT;
        }
        if (forward && right) {
            return TBT_LANE_RIGHT;
        }
        if (left && right) {
            return TBT_LANE_SPLIT;
        }
        if (left) {
            return TBT_LEFT;
        }
        if (right) {
            return TBT_RIGHT;
        }
        if (forward) {
            return TBT_STRAIGHT;
        }
        return TBT_NONE;
    }

    /**
     * Replacement for CarrotNaverBridge.safetyJson(): same JSON shape, TMAP type
     * and a limit only for speed-enforcing cameras.
     */
    public static String safetyJson(Object sdi) {
        Object code = call(sdi, "getCode");
        int naver = (int) number(call(code, "getValue"));
        String name = String.valueOf(code);
        int type = sdiType(naver, name);
        int limit = sdiLimitAllowed(type) ? speedLimit(sdi) : 0;
        return "{\"type\":" + type + ",\"naver_type\":" + naver + ",\"code\":\"" + esc(name)
                + "\",\"distance_m\":" + round(number(call(sdi, "distance")))
                + ",\"speed_limit_kph\":" + limit + "}";
    }

    // ---- small reflective helpers (same semantics as CarrotNaverBridge) ----

    static int speedLimit(Object obj) {
        return (int) Math.max(0, Math.round(number(callPrefix(obj, "getSpeedLimit"))));
    }

    static Object call(Object obj, String name) {
        if (obj == null) {
            return null;
        }
        try {
            Method m = obj.getClass().getMethod(name, new Class<?>[0]);
            m.setAccessible(true);
            return m.invoke(obj, new Object[0]);
        } catch (Throwable t) {
            return null;
        }
    }

    static Object callPrefix(Object obj, String prefix) {
        if (obj == null) {
            return null;
        }
        try {
            Method[] methods = obj.getClass().getMethods();
            for (int i = 0; i < methods.length; i++) {
                Method m = methods[i];
                if (m.getName().startsWith(prefix) && m.getParameterTypes().length == 0) {
                    m.setAccessible(true);
                    return m.invoke(obj, new Object[0]);
                }
            }
        } catch (Throwable ignored) {
        }
        return null;
    }

    static double number(Object obj) {
        if (obj instanceof Number) {
            return ((Number) obj).doubleValue();
        }
        if (obj == null) {
            return 0.0;
        }
        try {
            Field[] fields = obj.getClass().getDeclaredFields();
            for (int i = 0; i < fields.length; i++) {
                Field f = fields[i];
                if (Modifier.isStatic(f.getModifiers())) {
                    continue;
                }
                f.setAccessible(true);
                Object v = f.get(obj);
                if (v instanceof Number) {
                    return ((Number) v).doubleValue();
                }
            }
        } catch (Throwable ignored) {
        }
        return 0.0;
    }

    static long round(double d) {
        return Math.max(0, Math.round(d));
    }

    static String esc(String s) {
        if (s == null) {
            return "";
        }
        StringBuilder sb = new StringBuilder(s.length() + 8);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"' || c == '\\') {
                sb.append('\\').append(c);
            } else if (c == '\n') {
                sb.append("\\n");
            } else if (c == '\r') {
                sb.append("\\r");
            } else if (c == '\t') {
                sb.append("\\t");
            } else if (c < ' ') {
                sb.append(' ');
            } else {
                sb.append(c);
            }
        }
        return sb.toString();
    }
}
