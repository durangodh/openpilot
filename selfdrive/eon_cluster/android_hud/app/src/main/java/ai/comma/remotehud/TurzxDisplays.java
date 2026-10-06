package ai.comma.remotehud;

import android.content.Context;

/**
 * 두 세대의 TURZX 패널을 독립적으로 관리한다.
 *
 * 기존 9.7인치 연결이 살아 있는 동안 12.3인치를 추가할 수 있고, 어느 한쪽을
 * 분리해도 남은 패널의 스트리밍은 계속된다. 각 패널은 별도 USB connection과
 * endpoint 상태를 가지므로 한 패널의 오류가 다른 패널을 닫지 않는다.
 */
final class TurzxDisplays {

    private final TurzxDisplay legacy;
    private final TurzxDisplay wide;

    TurzxDisplays(Context context) {
        legacy = new TurzxDisplay(context, TurzxDisplay.PID_97, "9.7인치");
        wide = new TurzxDisplay(context, TurzxDisplay.PID_123, "12.3인치");
    }

    boolean isOpen() {
        return legacy.isOpen() || wide.isOpen();
    }

    boolean isLegacyOpen() {
        return legacy.isOpen();
    }

    boolean isWideOpen() {
        return wide.isOpen();
    }

    int openCount() {
        int count = 0;
        if (legacy.isOpen()) count++;
        if (wide.isOpen()) count++;
        return count;
    }

    String describeStatus() {
        return legacy.describeStatus() + " · " + wide.describeStatus();
    }

    boolean openOrRequestPermission() throws Exception {
        Exception first = null;
        try {
            legacy.openOrRequestPermission();
        } catch (Exception e) {
            first = e;
        }
        try {
            wide.openOrRequestPermission();
        } catch (Exception e) {
            if (first == null) first = e;
        }
        if (!isOpen() && first != null) {
            throw first;
        }
        return isOpen();
    }

    int openFailureStreak() {
        return Math.max(legacy.openFailureStreak(), wide.openFailureStreak());
    }

    String deviceNameOrNull() {
        String name = legacy.deviceNameOrNull();
        return name != null ? name : wide.deviceNameOrNull();
    }

    void setBrightness(int value) throws Exception {
        Exception first = null;
        int attempted = 0;
        int succeeded = 0;
        if (legacy.isOpen()) {
            attempted++;
            try {
                legacy.setBrightness(value);
                succeeded++;
            } catch (Exception e) {
                first = e;
                legacy.recoverAfterError();
                legacy.close();
            }
        }
        if (wide.isOpen()) {
            attempted++;
            try {
                wide.setBrightness(value);
                succeeded++;
            } catch (Exception e) {
                if (first == null) first = e;
                wide.recoverAfterError();
                wide.close();
            }
        }
        if (attempted > 0 && succeeded == 0 && first != null) {
            throw first;
        }
    }

    void sendJpegs(byte[] legacyJpeg, byte[] wideJpeg) throws Exception {
        Exception first = null;
        int attempted = 0;
        int succeeded = 0;
        if (legacy.isOpen() && legacyJpeg != null) {
            attempted++;
            try {
                legacy.sendJpeg(legacyJpeg);
                succeeded++;
            } catch (Exception e) {
                first = e;
                legacy.recoverAfterError();
                legacy.close();
            }
        }
        if (wide.isOpen() && wideJpeg != null) {
            attempted++;
            try {
                wide.sendJpeg(wideJpeg);
                succeeded++;
            } catch (Exception e) {
                if (first == null) first = e;
                wide.recoverAfterError();
                wide.close();
            }
        }
        if (attempted > 0 && succeeded == 0 && first != null) {
            throw first;
        }
    }

    boolean closeUnresponsive(long silenceMs) {
        boolean closed = false;
        if (legacy.isUnresponsive(silenceMs)) {
            legacy.close();
            closed = true;
        }
        if (wide.isUnresponsive(silenceMs)) {
            wide.close();
            closed = true;
        }
        return closed;
    }

    long silenceMs() {
        return Math.max(legacy.silenceMs(), wide.silenceMs());
    }

    void recoverAfterError() {
        legacy.recoverAfterError();
        wide.recoverAfterError();
    }

    void reset() {
        legacy.reset();
        wide.reset();
    }

    void close() {
        legacy.close();
        wide.close();
    }
}
