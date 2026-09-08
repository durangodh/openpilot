package com.naver.map.carrot;

/** Host-side checks for the Naver-to-TMAP HUD code adapter. */
public final class CarrotNaverCodesCheck {
    private static void eq(int expected, int actual, String label) {
        if (expected != actual) {
            throw new AssertionError(label + ": expected " + expected + ", got " + actual);
        }
    }

    private static final class Code {
        private final int value;
        private final String name;
        Code(int value, String name) { this.value = value; this.name = name; }
        public int getValue() { return value; }
        @Override public String toString() { return name; }
    }

    private static final class SpeedLimit {
        private final int value;
        SpeedLimit(int value) { this.value = value; }
    }

    private static final class Sdi {
        private final Code code;
        private final double distance;
        private final SpeedLimit speedLimit;
        Sdi(int code, String name, double distance, int limit) {
            this.code = new Code(code, name);
            this.distance = distance;
            this.speedLimit = new SpeedLimit(limit);
        }
        public Code getCode() { return code; }
        public double distance() { return distance; }
        public SpeedLimit getSpeedLimit() { return speedLimit; }
    }

    public static void main(String[] args) {
        eq(11, CarrotNaverCodes.turnType(1, "Straight"), "straight");
        eq(12, CarrotNaverCodes.turnType(2, "Left"), "left");
        eq(13, CarrotNaverCodes.turnType(3, "Right"), "right");
        eq(14, CarrotNaverCodes.turnType(6, "UTurn"), "uturn");
        eq(17, CarrotNaverCodes.turnType(4, "LeftDirection"), "keep left");
        eq(18, CarrotNaverCodes.turnType(5, "RightDirection"), "keep right");
        eq(131, CarrotNaverCodes.turnType(21, "RotaryStraight"), "rotary");
        eq(2, CarrotNaverCodes.turnType(88, "Goal"), "arrival");

        eq(20, CarrotNaverCodes.laneTurn("[Forward, Left]"), "forward-left lane");
        eq(21, CarrotNaverCodes.laneTurn("[Forward, Right]"), "forward-right lane");
        eq(22, CarrotNaverCodes.laneTurn("[Forward, Left, Right]"), "split lane");

        eq(1, CarrotNaverCodes.sdiType(1, "SpeedCam"), "speed camera");
        eq(2, CarrotNaverCodes.sdiType(12, "StartSectionSpeedCam"), "section start");
        eq(22, CarrotNaverCodes.sdiType(104, "SpeedBump"), "speed bump");
        String speed = CarrotNaverCodes.safetyJson(new Sdi(1, "SpeedCam", 125.6, 60));
        if (!speed.contains("\"type\":1") || !speed.contains("\"distance_m\":126")
                || !speed.contains("\"speed_limit_kph\":60")) {
            throw new AssertionError("speed-camera JSON: " + speed);
        }
        String info = CarrotNaverCodes.safetyJson(new Sdi(100, "SharpCurve", 50, 40));
        if (!info.contains("\"type\":99") || !info.contains("\"speed_limit_kph\":0")) {
            throw new AssertionError("informational JSON: " + info);
        }
        System.out.println("CarrotNaverCodes checks passed");
    }
}
