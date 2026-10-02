package ai.comma.tmaphud;

import java.io.File;
import java.io.FileWriter;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 티맵 프로세스 안에서 동작하는 상태/예외 로그.
 * 네이버 CarrotHudLog 와 같은 방식 — 티맵 자체 외부저장 폴더에 기록해
 * su 없이 파일 탐색기로 꺼내볼 수 있게 한다. 파일명은 .log 이지만 순수 텍스트라
 * 확장자를 .txt 로 바꿔 열면 카카오톡/뷰어로 공유 가능하다.
 *
 * 경로: /sdcard/Android/data/com.skt.tmap.ku/files/tmap_hud.log
 */
final class TmapHudLog {
    private static final String PATH =
            "/sdcard/Android/data/com.skt.tmap.ku/files/tmap_hud.log";
    private static final long MAX_BYTES = 512 * 1024;
    private static final SimpleDateFormat FMT =
            new SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US);
    private static final Object LOCK = new Object();
    private static long lastStatusMs = 0L;

    private TmapHudLog() {
    }

    static void line(String message) {
        synchronized (LOCK) {
            try {
                File file = new File(PATH);
                File dir = file.getParentFile();
                if (dir != null && !dir.exists()) {
                    // 티맵이 아직 files 폴더를 안 만들었을 수 있다.
                    dir.mkdirs();
                }
                if (file.exists() && file.length() > MAX_BYTES) {
                    // 단순 롤오버: 너무 커지면 새로 시작한다.
                    file.delete();
                }
                try (FileWriter writer = new FileWriter(file, true)) {
                    writer.write(FMT.format(new Date()));
                    writer.write("  ");
                    writer.write(message);
                    writer.write('\n');
                }
            } catch (Throwable ignored) {
                // 로그 실패는 절대 앱에 영향 주지 않는다.
            }
        }
    }

    /** 5초에 한 번만 찍는 상태줄(과도한 파일쓰기 방지). */
    static void status(String message) {
        long now = System.currentTimeMillis();
        synchronized (LOCK) {
            if (now - lastStatusMs < 5000L) {
                return;
            }
            lastStatusMs = now;
        }
        line(message);
    }

    static void ex(String where, Throwable t) {
        line(where + " EX " + t.getClass().getSimpleName() + ": " + t.getMessage());
    }
}
