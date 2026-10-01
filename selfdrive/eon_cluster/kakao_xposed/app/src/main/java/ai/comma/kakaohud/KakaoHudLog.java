package ai.comma.kakaohud;

import java.io.File;
import java.io.FileWriter;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * 카카오 프로세스 안에서 동작하는 상태/예외 로그.
 * 네이버 CarrotHudLog 와 같은 방식 — 카카오내비 자체 외부저장 폴더에 기록해
 * su 없이 파일 탐색기로 꺼내볼 수 있게 한다. 파일명은 .log 이지만 순수 텍스트라
 * 확장자를 .txt 로 바꿔 열면 카카오톡/뷰어로 공유 가능하다.
 *
 * 경로: /sdcard/Download/kakao_hud.log (우선)
 *       /sdcard/Android/data/com.locnall.KimGiSa/files/kakao_hud.log (Download 쓰기 실패 시)
 *
 * 안드로이드 11+(특히 16)에서는 다른 앱이 Android/data 안의 파일 내용을 읽지
 * 못해 파일관리자로 압축·공유하면 빈 파일이 된다. Download 는 앱이 권한 없이
 * 파일을 만들 수 있고 파일관리자도 읽을 수 있어 루트 없이 꺼낼 수 있다.
 */
final class KakaoHudLog {
    private static final String[] PATHS = {
            "/sdcard/Download/kakao_hud.log",
            "/sdcard/Android/data/com.locnall.KimGiSa/files/kakao_hud.log"};
    // 첫 성공 경로를 기억한다(-1 = 아직 모름). 실패하면 다음 경로로 넘어간다.
    private static int pathIndex = -1;
    private static final long MAX_BYTES = 512 * 1024;
    private static final SimpleDateFormat FMT =
            new SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US);
    private static final Object LOCK = new Object();
    private static long lastStatusMs = 0L;

    private KakaoHudLog() {
    }

    static void line(String message) {
        synchronized (LOCK) {
            int start = pathIndex < 0 ? 0 : pathIndex;
            for (int i = start; i < PATHS.length; i++) {
                if (append(PATHS[i], message)) {
                    pathIndex = i;
                    return;
                }
            }
            // 로그 실패는 절대 앱에 영향 주지 않는다.
        }
    }

    private static boolean append(String path, String message) {
        try {
            File file = new File(path);
            File dir = file.getParentFile();
            if (dir != null && !dir.exists()) {
                // 카카오가 아직 files 폴더를 안 만들었을 수 있다.
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
            return true;
        } catch (Throwable ignored) {
            return false;
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
