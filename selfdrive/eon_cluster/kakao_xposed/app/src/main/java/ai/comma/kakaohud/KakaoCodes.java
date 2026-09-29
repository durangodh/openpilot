package ai.comma.kakaohud;

/**
 * 카카오내비 안내 코드를 EON / Remote HUD 가 이해하는 TMAP 코드로 변환한다.
 *
 * 판정은 enum 이름(name())을 1순위로 쓴다. name() 은 java.lang.Enum 의 메서드라
 * 난독화되지 않고, 값은 생성자에 넘긴 문자열("KNRGCode_LeftTurn" 등)이라 앱
 * 업데이트에도 유지된다. 이름을 못 얻을 때만 raw 값(getValue)으로 대체한다.
 *
 * 회전 코드(EON navigation_route / onroad_navi 기준):
 *   11 직진 / 12 좌회전 / 13 우회전 / 14 유턴 / 16 급좌회전 / 17 좌측방향
 *   18 우측방향 / 19 급우회전 / 2 목적지 / 131..142 로터리
 *   Direction_1..12 는 방향각(directionAngle)으로 판정한다(TmapNda 방식).
 *
 * 카메라 코드: TMAP nSdiType 체계(TmapNda / carrot 와 동일).
 *   1 과속 / 2 구간시작 / 3 구간끝 / 4 구간중 / 6 신호 / 7 이동식 / 8 박스형
 *   9 버스전용 / 11 갓길 / 12 끼어들기 / 15 과적 / 16 적재불량 / 17 주차
 *   19 철길 / 20 어린이보호구역 / 22 방지턱 / 99 기타
 *   신호+과속은 1(과속)로 보낸다 — 속도 단속이 핵심이고, 일부 소비측은 0 을
 *   "카메라 없음"으로 본다.
 */
final class KakaoCodes {

    static final int TBT_NONE = 0;
    static final int TBT_ARRIVE = 2;
    static final int TBT_STRAIGHT = 11;
    static final int TBT_LEFT = 12;
    static final int TBT_RIGHT = 13;
    static final int TBT_UTURN = 14;
    static final int TBT_SHARP_LEFT = 16;
    static final int TBT_LEFT_DIR = 17;
    static final int TBT_RIGHT_DIR = 18;
    static final int TBT_SHARP_RIGHT = 19;
    static final int TBT_ROTARY_BASE = 131;

    static final int SDI_SPEED = 1;
    static final int SDI_SECTION_START = 2;
    static final int SDI_SECTION_END = 3;
    static final int SDI_SECTION_ON = 4;
    static final int SDI_SIGNAL = 6;
    static final int SDI_MOVABLE = 7;
    static final int SDI_BOXED = 8;
    static final int SDI_BUS = 9;
    static final int SDI_SHOULDER = 11;
    static final int SDI_CUT_IN = 12;
    static final int SDI_OVERLOAD = 15;
    static final int SDI_CARGO = 16;
    static final int SDI_PARKING = 17;
    static final int SDI_RAILROAD = 19;
    static final int SDI_CHILDREN = 20;
    static final int SDI_SPEED_BUMP = 22;
    static final int SDI_INFO = 99;

    private KakaoCodes() {
    }

    // ------------------------------------------------------------------ 회전

    /**
     * @param rgName  KNRGCode enum name() (예: "KNRGCode_LeftTurn"). null 이면 raw 사용.
     * @param rgRaw   KNRGCode raw 값(getValue). 모르면 -1.
     * @param angle   방향각(도). 모르면 음수.
     */
    static int turnType(String rgName, int rgRaw, int angle) {
        if (rgName != null && !rgName.isEmpty()) {
            int byName = turnByName(stripPrefix(rgName, "KNRGCode_"), angle);
            if (byName != TBT_NONE) return byName;
        }
        return turnByRaw(rgRaw, angle);
    }

    private static int turnByName(String n, int angle) {
        if (n.startsWith("RotaryDirection_")) return rotary(suffixIndex(n));
        if (n.startsWith("RoundaboutDirection_")) return rotary(suffixIndex(n));
        if (n.startsWith("Direction_")) return angle >= 0 ? fromAngle(angle) : TBT_STRAIGHT;
        switch (n) {
            case "Goal":
                return TBT_ARRIVE;
            case "Start":
            case "Straight":
                return TBT_STRAIGHT;
            case "LeftTurn":
            case "UnprotectedLeftTurn":   // 비보호좌회전은 좌회전이다(유턴 아님)
                return TBT_LEFT;
            case "RightTurn":
                return TBT_RIGHT;
            case "UTurn":
                return TBT_UTURN;
            default:
                break;
        }
        // 좌/우 분기·진출입·차로변경·측면(터널/고가/지하 옆)은 방향만 살린다.
        if (n.startsWith("Left")) return TBT_LEFT_DIR;
        if (n.startsWith("Right")) return TBT_RIGHT_DIR;
        if (n.equals("ChangeLeftHighway")) return TBT_LEFT_DIR;
        if (n.equals("ChangeRightHighway")) return TBT_RIGHT_DIR;
        // 톨게이트·터널·고가·지하·페리·고속도로 진출입(직진) 등은 직진 화살표.
        return TBT_STRAIGHT;
    }

    private static int turnByRaw(int rgRaw, int angle) {
        switch (rgRaw) {
            case 100: case 0: return TBT_STRAIGHT;
            case 101: return TBT_ARRIVE;
            case 1: case 63: return TBT_LEFT;       // 63 = UnprotectedLeftTurn
            case 2: return TBT_RIGHT;
            case 3: return TBT_UTURN;
            case 5: case 8: case 11: case 43: case 46: case 48: case 82:
                return TBT_LEFT_DIR;
            case 6: case 9: case 12: case 44: case 47: case 49: case 83:
                return TBT_RIGHT_DIR;
            default:
                break;
        }
        if (rgRaw >= 18 && rgRaw <= 29) return angle >= 0 ? fromAngle(angle) : TBT_STRAIGHT;
        if (rgRaw >= 30 && rgRaw <= 41) return rotary(rgRaw - 30);
        if (rgRaw >= 70 && rgRaw <= 81) return rotary(rgRaw - 70);
        return rgRaw < 0 ? TBT_NONE : TBT_STRAIGHT;
    }

    /** 방향각 → 회전 코드. TmapNda KakaoToTmapTurn.fromAngle 과 같은 구간. */
    static int fromAngle(int rawAngle) {
        int a = ((rawAngle % 360) + 360) % 360;
        if (a <= 20 || a >= 340) return TBT_STRAIGHT;
        if (a <= 60) return TBT_RIGHT_DIR;
        if (a <= 120) return TBT_RIGHT;
        if (a < 180) return TBT_SHARP_RIGHT;
        if (a == 180) return TBT_UTURN;
        if (a < 240) return TBT_SHARP_LEFT;
        if (a < 300) return TBT_LEFT;
        return TBT_LEFT_DIR;
    }

    private static int rotary(int index) {
        return TBT_ROTARY_BASE + Math.max(0, Math.min(11, index));
    }

    /** "RotaryDirection_3" → 2 (0-based). 실패하면 0. */
    private static int suffixIndex(String n) {
        int p = n.lastIndexOf('_');
        if (p < 0) return 0;
        try {
            return Integer.parseInt(n.substring(p + 1)) - 1;
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    // ------------------------------------------------------------------ 카메라

    static int sdiType(String safetyName, int safetyRaw) {
        if (safetyName != null && !safetyName.isEmpty()) {
            return sdiByName(stripPrefix(safetyName, "KNSafetyCode_"));
        }
        return sdiByRaw(safetyRaw);
    }

    private static int sdiByName(String n) {
        if (n.equals("Hump")) return SDI_SPEED_BUMP;
        if (n.startsWith("SpeedViolationSectionIn")) return SDI_SECTION_START;
        if (n.startsWith("SpeedViolationSectionOut")) return SDI_SECTION_END;
        if (n.equals("SpeedViolationSection") || n.equals("SpeedViolationSectionHalf")) {
            return SDI_SECTION_ON;
        }
        if (n.startsWith("MovableSpeedViolation")) return SDI_MOVABLE;
        if (n.startsWith("BoxedSpeedViolation")) return SDI_BOXED;
        if (n.startsWith("SpeedViolation")) return SDI_SPEED;
        // 과속을 함께 단속하는 복합 카메라는 속도 단속으로 본다.
        if (n.startsWith("SignalAndSpeed") || n.startsWith("LaneAndSpeed")
                || n.startsWith("BuslaneAndSpeed")) {
            return SDI_SPEED;
        }
        if (n.startsWith("SignalViolation")) return SDI_SIGNAL;
        if (n.startsWith("BuslaneViolation") || n.startsWith("BusLaneViolation")) return SDI_BUS;
        if (n.startsWith("ShoulderLane")) return SDI_SHOULDER;
        if (n.startsWith("CutIn")) return SDI_CUT_IN;
        if (n.startsWith("Overload")) return SDI_OVERLOAD;
        if (n.startsWith("Cargo")) return SDI_CARGO;
        if (n.startsWith("Parking")) return SDI_PARKING;
        if (n.startsWith("RailroadCrossing")) return SDI_RAILROAD;
        if (n.startsWith("ChildrenProtection")) return SDI_CHILDREN;
        return SDI_INFO;
    }

    /** name() 을 못 얻었을 때만 쓰는 raw 값 표(KNSafetyCode 4.51.0 실값). */
    private static int sdiByRaw(int r) {
        switch (r) {
            case 6: return SDI_SPEED_BUMP;
            case 92: case 98: case 105: case 692: case 705: return SDI_SECTION_START;
            case 93: case 99: case 106: case 693: case 706: return SDI_SECTION_END;
            case 96: case 696: return SDI_SECTION_ON;
            case 81: return SDI_MOVABLE;
            case 100: return SDI_BOXED;
            case 80: case 82: case 102: case 86: case 103: case 89: case 91: case 108:
                return SDI_SPEED;
            case 90: return SDI_SIGNAL;
            case 84: case 107: return SDI_BUS;
            case 94: return SDI_SHOULDER;
            case 95: return SDI_CUT_IN;
            case 85: return SDI_OVERLOAD;
            case 88: return SDI_CARGO;
            case 87: return SDI_PARKING;
            case 10: return SDI_RAILROAD;
            case 11: return SDI_CHILDREN;
            default: return SDI_INFO;
        }
    }

    static boolean isSection(int sdi) {
        return sdi == SDI_SECTION_START || sdi == SDI_SECTION_END || sdi == SDI_SECTION_ON;
    }

    /** 감속 대상이 될 수 있는 항목인지(방지턱 또는 제한속도가 있는 단속). */
    static boolean isSpeedRelevant(int sdi, int limitKph) {
        return sdi == SDI_SPEED_BUMP || limitKph > 0;
    }

    private static String stripPrefix(String s, String prefix) {
        return s.startsWith(prefix) ? s.substring(prefix.length()) : s;
    }
}
