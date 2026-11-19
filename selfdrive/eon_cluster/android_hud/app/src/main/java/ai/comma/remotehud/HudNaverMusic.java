package ai.comma.remotehud;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.Build;
import android.os.SystemClock;

/** Receives Naver's private player metadata from the LSPosed module on this phone. */
final class HudNaverMusic {
    private static final String NAVER = "com.nhn.android.nmap";
    private static final String UPDATE = "ai.comma.remotehud.NAVER_MUSIC";
    private static final String REQUEST = "ai.comma.remotehud.NAVER_MUSIC_REQUEST";
    private static volatile Snapshot latest;
    private final Context context;
    private boolean registered;

    private static final class Snapshot {
        final String song;
        final int state;
        final long sentAt;

        Snapshot(String song, int state, long sentAt) {
            this.song = song;
            this.state = state;
            this.sentAt = sentAt;
        }
    }

    HudNaverMusic(Context context) { this.context = context; }

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context ignored, Intent intent) {
            if (!UPDATE.equals(intent.getAction())) return;
            long sentAt = intent.getLongExtra("sentAt", -1L);
            long age = SystemClock.elapsedRealtime() - sentAt;
            // Cached broadcasts must not resurrect a song after the module stopped.
            if (sentAt < 0 || age < 0 || age > 35000L) return;
            Snapshot previous = latest;
            if (previous != null && sentAt < previous.sentAt) return;
            String title = clean(intent.getStringExtra("title"));
            String artist = clean(intent.getStringExtra("artist"));
            int state = intent.getIntExtra("state", 0);
            if (state == 0 || state == 1 || state == 7) title = "";
            String song = title.isEmpty() ? "" : title + (artist.isEmpty() ? "" : " · " + artist);
            latest = new Snapshot(song, state, sentAt);
        }
    };

    void start() {
        if (registered) return;
        IntentFilter filter = new IntentFilter(UPDATE);
        if (Build.VERSION.SDK_INT >= 33) {
            context.registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED);
        } else {
            context.registerReceiver(receiver, filter);
        }
        registered = true;
        // The player may already be running when the HUD starts/restarts.
        context.sendBroadcast(new Intent(REQUEST).setPackage(NAVER));
    }

    void stop() {
        if (registered) context.unregisterReceiver(receiver);
        registered = false;
        latest = null;
    }

    static String nowPlaying() {
        Snapshot song = latest;
        String publicSong = HudMusicService.nowPlaying;
        boolean publicPlaying = HudMusicService.sessionPlaying;
        String publicPackage = HudMusicService.sessionPackage;
        if (song == null || SystemClock.elapsedRealtime() - song.sentAt > 35000L
                || song.song.isEmpty()) return publicSong;
        // An actively playing standalone Bugs/Spotify session wins over old Naver metadata.
        if (publicPlaying && !publicSong.isEmpty() && !NAVER.equals(publicPackage)) return publicSong;
        if (song.state == 2 && !publicSong.isEmpty() && publicPlaying) return publicSong;
        return song.song;
    }

    static String status() {
        Snapshot song = latest;
        if (song == null || SystemClock.elapsedRealtime() - song.sentAt > 35000L) {
            return "네이버 음악: 모듈 연결 대기";
        }
        return song.song.isEmpty() ? "네이버 음악: 모듈 연결됨 · 곡 정보 대기"
                : "네이버 음악: 수신 중 · " + song.song;
    }

    private static String clean(String value) {
        if (value == null) return "";
        value = value.replace('\n', ' ').replace('\r', ' ').trim();
        return value.codePointCount(0, value.length()) <= 200 ? value
                : value.substring(0, value.offsetByCodePoints(0, 200));
    }
}
