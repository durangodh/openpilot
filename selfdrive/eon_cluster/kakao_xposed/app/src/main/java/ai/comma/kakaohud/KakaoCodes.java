package ai.comma.kakaohud;

/**
 * 카카오내비(KNSDK) 안내 코드를 EON / Remote HUD 가 이미 이해하는 TMAP 코드로
 * 변환한다. 네이버의 CarrotNaverCodes 와 같은 역할.
 *
 * 카카오 KNRGCode 의 "실값"(enum 두 번째 정수)은 TMAP 회전 코드와 거의 동일한
 * 배치라, 대부분 그대로 통과시키고 몇 개만 보정한다.
 *
 * TMAP TBT 코드(navigation_route.py / onroad_navi.inc carrotTurnDirection):
 *   12 좌회전 / 13 우회전 / 14 유턴 / 2 목적지 / 11 직진
 *   16,17 좌측방향 / 18,19 우측방향 / 20 직진+좌 / 21 직진+우 / 22 좌우
 *   131..142 로터리
 *
 * 카카오 KNRGCode 실값(raw):
 *   Straight=0 LeftTurn=1 RightTurn=2 UTurn=3 LeftDirection=5 RightDirection=6
 *   Direction_1..12=18..29  RotaryDirection_1..12=30..41
 *   RoundaboutDirection_1..12=70..81
 *   Goal=101 Start=100 Via=1000
 */
final class KakaoCodes {

    // TMAP TBT codes
    static final int TBT_NONE = 0;
    static final int TBT_ARRIVE = 2;
    static final int TBT_STRAIGHT = 11;
    static final int TBT_LEFT = 12;
    static final int TBT_RIGHT = 13;
    static final int TBT_UTURN = 14;
    static final int TBT_LEFT_DIR = 17;   // 좌측 방향(분기, EON FORK_LEFT)
    static final int TBT_RIGHT_DIR = 18;  // 우측 방향(분기, EON FORK_RIGHT)
    static final int TBT_ROTARY_BASE = 131;

    // TMAP SDI types (cruise_helper 는 22=방지턱만 특별 취급)
    static final int SDI_SIGNAL_SPEED = 0;
    static final int SDI_SPEED = 1;
    static final int SDI_SECTION_START = 2;
    static final int SDI_SECTION_END = 3;
    static final int SDI_BUS = 4;
    static final int SDI_OTHER_CAM = 5;
    static final int SDI_SPEED_BUMP = 22;
    static final int SDI_INFO = 99;

    private KakaoCodes() {
    }

    /** KNRGCode raw value -> TMAP TBT code. */
    static int turnType(int rgRaw) {
        switch (rgRaw) {
            case 100: // Start
            case 0:   // Straight
                return TBT_STRAIGHT;
            case 101: // Goal
                return TBT_ARRIVE;
            case 1:   // LeftTurn
                return TBT_LEFT;
            case 2:   // RightTurn
                return TBT_RIGHT;
            case 3:   // UTurn
            case 63:  // UnprotectedLeftTurn (유턴 계열로 처리)
                return TBT_UTURN;
            case 5:   // LeftDirection
            case 8:   // LeftOutHighway
            case 11:  // LeftInHighway
            case 43:  // LeftOutCityway
            case 46:  // LeftInCityway
            case 48:  // ChangeLeftHighway
            case 82:  // LeftStraight
            case 86:  // JoinAfterBranch(좌측 합류로 근사)
                return TBT_LEFT_DIR;
            case 6:   // RightDirection
            case 9:   // RightOutHighway
            case 12:  // RightInHighway
            case 44:  // RightOutCityway
            case 47:  // RightInCityway
            case 49:  // ChangeRightHighway
            case 83:  // RightStraight
                return TBT_RIGHT_DIR;
            default:
                break;
        }
        // Direction_1..12 (18..29): 시계방향 안내. 좌/우로 근사.
        if (rgRaw >= 18 && rgRaw <= 29) {
            int clock = rgRaw - 18; // 0..11 = 1시~12시 계열
            // 대략 6시 이후(뒤쪽)은 유턴, 앞쪽 좌/우로 나눔.
            if (clock <= 4) return TBT_RIGHT_DIR;
            if (clock >= 7) return TBT_LEFT_DIR;
            return TBT_STRAIGHT;
        }
        // RotaryDirection_1..12 (30..41), RoundaboutDirection_1..12 (70..81)
        if (rgRaw >= 30 && rgRaw <= 41) {
            return rotary(rgRaw - 30);
        }
        if (rgRaw >= 70 && rgRaw <= 81) {
            return rotary(rgRaw - 70);
        }
        return TBT_NONE;
    }

    private static int rotary(int index) {
        return TBT_ROTARY_BASE + Math.max(0, Math.min(11, index));
    }

    /**
     * KNSignCode raw value -> TMAP SDI type.
     * 카메라/안전표지. raw 값은 KNUSignCode 실측표 기준.
     *   Hump=6, 과속 계열 80/82/100, 신호+과속=86, 구간 92/93, 버스=84 등.
     */
    static int sdiType(int signRaw) {
        switch (signRaw) {
            case 6:   // Hump (방지턱)
                return SDI_SPEED_BUMP;
            case 80:  // ViolationCamera (일반 과속류)
            case 81:  // MovableSpeedViolationCamera
            case 82:  // SpeedViolationCamera
            case 100: // BoxedSpeedViolationCamera
            case 102: // SpeedViolationBackwardCamera
                return SDI_SPEED;
            case 86:  // SignalAndSpeedViolationCamera (신호+과속)
            case 90:  // SignalViolationCamera
            case 103: // SignalAndSpeedViolationBackwardCamera
                return SDI_SIGNAL_SPEED;
            case 92:  // SpeedViolationSectionInCamera (구간 시작)
            case 98:  // LandChangeViolationSectionInCamera
            case 105: // SpeedViolationSectionInBackwardCamera
                return SDI_SECTION_START;
            case 93:  // SpeedViolationSectionOutCamera (구간 끝)
            case 99:  // LandChangeViolationSectionOutCamera
            case 106: // SpeedViolationSectionOutBackwardCamera
                return SDI_SECTION_END;
            case 84:  // BuslaneViolationCamera (버스전용)
            case 107: // BusLaneViolationBackwardCamera
                return SDI_BUS;
            case 85:  // OverloadViolationCamera
            case 87:  // ParkingViolationCamera
            case 88:  // CargoViolationCamera
            case 89:  // BuslaneAndSpeedViolationCamera
            case 91:  // LaneAndSpeedViolationCamera
            case 94:  // ShoulderLaneViolationCamera
            case 95:  // CutInViolationCamera
            case 97:  // DrivingLaneViolationCamera
            case 101: // SeatBeltViolationCamera
            case 104: // OldDieselCamera
                return SDI_OTHER_CAM;
            default:
                return SDI_INFO;
        }
    }

    /** 제한속도를 붙일 수 있는 카메라 종류인지(정보성/방지턱 제외). */
    static boolean sdiHasLimit(int tmapType) {
        return tmapType == SDI_SPEED || tmapType == SDI_SIGNAL_SPEED
                || tmapType == SDI_SECTION_START || tmapType == SDI_SECTION_END;
    }
}
