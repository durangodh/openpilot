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
 * 경로: /sdcard/Android/data/com.locnall.KimGiSa/files/kakao_hud.log
 */
final class KakaoHudLog {
    private static final String PATH =
            "/sdcard/Android/data/com.locnall.KimGiSa/files/kakao_hud.log";
    private static final long MAX_BYTES = 512 * 1024;
    private static final SimpleDateFormat FMT =
            new SimpleDateFormat("MM-dd HH:mm:ss.SSS", Locale.US);
    private static final Object LOCK = new Object();
    private static long lastStatusMs = 0L;

    // 음성 버튼 진단 줄 등은 EON 기록 페이지(http://EON_IP:7714/trace, kakao_날짜.log)에서도
    // 받을 수 있게 상태 소켓으로 보낸다. 안내 전송을 방해하지 않도록 고른 줄만, 초당 5줄까지.
    private static volatile KakaoNaviClient forward;
    private static final java.util.ArrayDeque<String> pending = new java.util.ArrayDeque<>();
    private static long forwardWindowMs = 0L;
    private static int forwardCount = 0;

    static void forwardTo(KakaoNaviClient client) {
        forward = client;
        // 초당 한도에 걸려 남은 줄과, EON 주소를 받기 전에 쌓인 줄을 1초마다 내보낸다.
        java.util.concurrent.Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "kakao-hud-log-forward");
            t.setDaemon(true);
            return t;
        }).scheduleWithFixedDelay(() -> {
            try { forward(null); } catch (Throwable ignored) { }
        }, 1, 1, java.util.concurrent.TimeUnit.SECONDS);
    }

    private static boolean forwarded(String message) {
        return message.startsWith("voice") || message.startsWith("===")
                || message.startsWith("KakaoNavi version");
    }

    private static void forward(String stamped) {
        KakaoNaviClient client = forward;
        synchronized (pending) {
            if (stamped != null) {
                pending.addLast(stamped);
                while (pending.size() > 100) pending.removeFirst();
            }
            if (client == null || !client.ready()) return;
            long now = System.currentTimeMillis();
            if (now - forwardWindowMs >= 1000L) {
                forwardWindowMs = now;
                forwardCount = 0;
            }
            while (!pending.isEmpty() && forwardCount < 5) {
                client.sendState("module_log", jsonString(pending.removeFirst()));
                forwardCount++;
            }
        }
    }

    static String jsonString(String s) {
        StringBuilder sb = new StringBuilder(s.length() + 2).append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"' || c == '\\') sb.append('\\').append(c);
            else if (c < 0x20) sb.append(' ');
            else sb.append(c);
        }
        return sb.append('"').toString();
    }

    private KakaoHudLog() {
    }

    /** 파일 로그와 함께 LSPosed 로그(logcat)에도 남긴다. 파일을 꺼내기 어려울 때 확인용. */
    static void xposed(String message) {
        try {
            de.robv.android.xposed.XposedBridge.log("KakaoHud: " + message);
        } catch (Throwable ignored) {
            // Xposed 밖
        }
        line(message);
    }

    static void line(String message) {
        synchronized (LOCK) {
            try {
                File file = new File(PATH);
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
            } catch (Throwable ignored) {
                // 로그 실패는 절대 앱에 영향 주지 않는다.
            }
        }
        try {
            forward(message != null && forwarded(message) ? message : null);
        } catch (Throwable ignored) {
            // 전송 실패도 앱에 영향 없음
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
