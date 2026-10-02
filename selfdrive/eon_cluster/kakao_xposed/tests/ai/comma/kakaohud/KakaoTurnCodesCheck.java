package ai.comma.kakaohud;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/**
 * KakaoCodes.turnType for every KNRGCode of KakaoNavi 4.51.1 (92 codes, from the
 * decompiled com.kakaomobility.knsdk.KNRGCode). Checks that
 *  - the name path (enum name()) and the raw path (getValue) agree,
 *  - each code lands on the intended TMAP TBT code, and
 *  - the EON class (navigation_route.classify) is the expected one.
 * Run: javac -d out KakaoCodes.java KakaoTurnCodesCheck.java && java -cp out ai.comma.kakaohud.KakaoTurnCodesCheck
 */
public final class KakaoTurnCodesCheck {
    // {enum name, raw value, expected TMAP TBT}
    private static final String[][] CODES = {
        {"KNRGCode_Start", "100", "11"},
        {"KNRGCode_Goal", "101", "2"},
        {"KNRGCode_Via", "1000", "11"},
        {"KNRGCode_Straight", "0", "11"},
        {"KNRGCode_LeftTurn", "1", "12"},
        {"KNRGCode_RightTurn", "2", "13"},
        {"KNRGCode_UTurn", "3", "14"},
        {"KNRGCode_LeftDirection", "5", "17"},
        {"KNRGCode_RightDirection", "6", "18"},
        {"KNRGCode_OutHighway", "7", "11"},
        {"KNRGCode_LeftOutHighway", "8", "17"},
        {"KNRGCode_RightOutHighway", "9", "18"},
        {"KNRGCode_InHighway", "10", "11"},
        {"KNRGCode_LeftInHighway", "11", "17"},
        {"KNRGCode_RightInHighway", "12", "18"},
        {"KNRGCode_OverPath", "14", "11"},
        {"KNRGCode_UnderPath", "15", "11"},
        {"KNRGCode_OverPathSide", "16", "11"},
        {"KNRGCode_UnderPathSide", "17", "11"},
        {"KNRGCode_Direction_1", "18", "18"},
        {"KNRGCode_Direction_2", "19", "18"},
        {"KNRGCode_Direction_3", "20", "13"},
        {"KNRGCode_Direction_4", "21", "19"},
        {"KNRGCode_Direction_5", "22", "19"},
        {"KNRGCode_Direction_6", "23", "14"},
        {"KNRGCode_Direction_7", "24", "16"},
        {"KNRGCode_Direction_8", "25", "16"},
        {"KNRGCode_Direction_9", "26", "12"},
        {"KNRGCode_Direction_10", "27", "17"},
        {"KNRGCode_Direction_11", "28", "17"},
        {"KNRGCode_Direction_12", "29", "11"},
        {"KNRGCode_RotaryDirection_1", "30", "131"},
        {"KNRGCode_RotaryDirection_2", "31", "132"},
        {"KNRGCode_RotaryDirection_3", "32", "133"},
        {"KNRGCode_RotaryDirection_4", "33", "134"},
        {"KNRGCode_RotaryDirection_5", "34", "135"},
        {"KNRGCode_RotaryDirection_6", "35", "136"},
        {"KNRGCode_RotaryDirection_7", "36", "137"},
        {"KNRGCode_RotaryDirection_8", "37", "138"},
        {"KNRGCode_RotaryDirection_9", "38", "139"},
        {"KNRGCode_RotaryDirection_10", "39", "140"},
        {"KNRGCode_RotaryDirection_11", "40", "141"},
        {"KNRGCode_RotaryDirection_12", "41", "142"},
        {"KNRGCode_OutCityway", "42", "11"},
        {"KNRGCode_LeftOutCityway", "43", "17"},
        {"KNRGCode_RightOutCityway", "44", "18"},
        {"KNRGCode_InCityway", "45", "11"},
        {"KNRGCode_LeftInCityway", "46", "17"},
        {"KNRGCode_RightInCityway", "47", "18"},
        {"KNRGCode_ChangeLeftHighway", "48", "17"},
        {"KNRGCode_ChangeRightHighway", "49", "18"},
        {"KNRGCode_InFerry", "61", "11"},
        {"KNRGCode_OutFerry", "62", "11"},
        {"KNRGCode_UnprotectedLeftTurn", "63", "12"},
        {"KNRGCode_Tunnel", "64", "11"},
        {"KNRGCode_TunnelSide", "65", "11"},
        {"KNRGCode_LeftTunnel", "66", "17"},
        {"KNRGCode_LeftTunnelSide", "67", "17"},
        {"KNRGCode_RightTunnel", "68", "18"},
        {"KNRGCode_RightTunnelSide", "69", "18"},
        {"KNRGCode_RoundaboutDirection_1", "70", "131"},
        {"KNRGCode_RoundaboutDirection_2", "71", "132"},
        {"KNRGCode_RoundaboutDirection_3", "72", "133"},
        {"KNRGCode_RoundaboutDirection_4", "73", "134"},
        {"KNRGCode_RoundaboutDirection_5", "74", "135"},
        {"KNRGCode_RoundaboutDirection_6", "75", "136"},
        {"KNRGCode_RoundaboutDirection_7", "76", "137"},
        {"KNRGCode_RoundaboutDirection_8", "77", "138"},
        {"KNRGCode_RoundaboutDirection_9", "78", "139"},
        {"KNRGCode_RoundaboutDirection_10", "79", "140"},
        {"KNRGCode_RoundaboutDirection_11", "80", "141"},
        {"KNRGCode_RoundaboutDirection_12", "81", "142"},
        {"KNRGCode_LeftStraight", "82", "17"},
        {"KNRGCode_RightStraight", "83", "18"},
        {"KNRGCode_Tollgate", "84", "11"},
        {"KNRGCode_NonstopTollgate", "85", "11"},
        {"KNRGCode_JoinAfterBranch", "86", "11"},
        {"KNRGCode_LeftOverPath", "87", "17"},
        {"KNRGCode_LeftOverPathSide", "88", "17"},
        {"KNRGCode_RightOverPath", "89", "18"},
        {"KNRGCode_RightOverPathSide", "90", "18"},
        {"KNRGCode_LeftUnderPath", "91", "17"},
        {"KNRGCode_LeftUnderPathSide", "92", "17"},
        {"KNRGCode_RightUnderPath", "93", "18"},
        {"KNRGCode_RightUnderPathSide", "94", "18"},
        {"KNRGCode_IndoorEnterance", "900", "11"},
        {"KNRGCode_IndoorExit", "901", "11"},
        {"KNRGCode_IndoorToUpFloor", "902", "11"},
        {"KNRGCode_IndoorToDownFloor", "903", "11"},
        {"KNRGCode_IndoorToAdjacentParkingLot", "904", "11"},
        {"KNRGCode_IndoorFromAdjacentParkingLot", "905", "11"},
        {"KNRGCode_IndoorRotationPoint", "906", "11"},
    };

    // selfdrive/controls/lib/navigation_route.py
    private static final Set<Integer> TURN_LEFT = new HashSet<>(Arrays.asList(12, 16, 1000));
    private static final Set<Integer> TURN_RIGHT = new HashSet<>(Arrays.asList(13, 19, 1001));
    private static final Set<Integer> FORK_LEFT = new HashSet<>(Arrays.asList(7, 17, 44, 75, 76, 102, 105, 112, 115, 118, 1002, 1006));
    private static final Set<Integer> FORK_RIGHT = new HashSet<>(Arrays.asList(6, 18, 43, 73, 74, 101, 104, 111, 114, 117, 123, 124, 1003, 1007));

    static String eonClass(int tbt) {
        if (TURN_LEFT.contains(tbt)) return "turn-left";
        if (TURN_RIGHT.contains(tbt)) return "turn-right";
        if (FORK_LEFT.contains(tbt)) return "fork-left";
        if (FORK_RIGHT.contains(tbt)) return "fork-right";
        if (tbt == 14) return "uturn";
        if (tbt >= 131 && tbt <= 142) return "rotary";
        return "none";   // straight (11) and arrival (2) are not maneuvers for EON
    }

    public static void main(String[] args) {
        if (CODES.length != 92) throw new AssertionError("expected 92 KNRGCode values, got " + CODES.length);
        int failures = 0;
        for (String[] row : CODES) {
            String name = row[0];
            int raw = Integer.parseInt(row[1]);
            int expected = Integer.parseInt(row[2]);
            int byName = KakaoCodes.turnType(name, -1, -1);
            int byRaw = KakaoCodes.turnType(null, raw, -1);
            int withAngle = KakaoCodes.turnType(name, raw, 45);   // an unrelated angle must not change it
            if (byName != expected || byRaw != expected || withAngle != expected) {
                failures++;
                System.out.println("FAIL " + name + "(" + raw + "): name=" + byName + " raw=" + byRaw
                        + " angle=" + withAngle + " expected=" + expected);
            }
        }
        // Spot-check the EON classes of the maneuvers that matter most.
        expectClass("KNRGCode_LeftTurn", "turn-left");
        expectClass("KNRGCode_UnprotectedLeftTurn", "turn-left");
        expectClass("KNRGCode_RightTurn", "turn-right");
        expectClass("KNRGCode_UTurn", "uturn");
        expectClass("KNRGCode_LeftOutHighway", "fork-left");
        expectClass("KNRGCode_RightInCityway", "fork-right");
        expectClass("KNRGCode_Direction_9", "turn-left");
        expectClass("KNRGCode_Direction_4", "turn-right");
        expectClass("KNRGCode_Direction_1", "fork-right");
        expectClass("KNRGCode_Direction_12", "none");
        expectClass("KNRGCode_RotaryDirection_3", "rotary");
        expectClass("KNRGCode_RoundaboutDirection_12", "rotary");
        expectClass("KNRGCode_Goal", "none");
        expectClass("KNRGCode_Tollgate", "none");
        if (KakaoCodes.turnType("KNRGCode_Goal", 101, -1) != 2) throw new AssertionError("Goal must be arrival (2)");
        if (KakaoCodes.turnType(null, -1, -1) != 0) throw new AssertionError("unknown must be none (0)");
        if (failures > 0) throw new AssertionError(failures + " KNRGCode mappings differ");
        System.out.println("KakaoTurnCodesCheck: all 92 KNRGCode values map consistently (name, raw, EON class)");
    }

    private static void expectClass(String name, String cls) {
        String got = eonClass(KakaoCodes.turnType(name, -1, -1));
        if (!got.equals(cls)) throw new AssertionError(name + ": EON class " + got + " != " + cls);
    }
}
