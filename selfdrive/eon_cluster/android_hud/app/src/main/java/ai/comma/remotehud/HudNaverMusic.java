package ai.comma.remotehud;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.Build;
import android.os.SystemClock;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;

/** Receives Naver's private player metadata from the LSPosed module on this phone. */
final class HudNaverMusic {
    private static final String NAVER = "com.nhn.android.nmap";
    private static final String UPDATE = "ai.comma.remotehud.NAVER_MUSIC";
    private static final String REQUEST = "ai.comma.remotehud.NAVER_MUSIC_REQUEST";
    private static volatile Snapshot latest;
    private static int activeReceivers;
    // Same-phone UDP from the Naver module (127.0.0.1). Unlike a cross-app broadcast, Android's
    // package visibility rules cannot filter it out; the broadcast stays as a second path.
    static final int LOCAL_PORT = 7215;
    private static DatagramSocket localSocket;
    private final Context context;
    private boolean registered;

    private static final class Snapshot {
        final String song;
        final int state;
        final long sentAt;
        final int hooks;

        Snapshot(String song, int state, long sentAt, int hooks) {
            this.song = song;
            this.state = state;
            this.sentAt = sentAt;
            this.hooks = hooks;
        }
    }

    HudNaverMusic(Context context) { this.context = context; }

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context ignored, Intent intent) {
            if (!UPDATE.equals(intent.getAction())) return;
            accept(intent.getStringExtra("title"), intent.getStringExtra("artist"),
                    intent.getIntExtra("state", 0), intent.getLongExtra("sentAt", -1L),
                    intent.getIntExtra("hooks", -1));
        }
    };

    private static synchronized void accept(String rawTitle, String rawArtist, int state, long sentAt, int hooks) {
        long age = SystemClock.elapsedRealtime() - sentAt;
        // Cached broadcasts must not resurrect a song after the module stopped.
        if (sentAt < 0 || age < 0 || age > 35000L) return;
        Snapshot previous = latest;
        if (previous != null && sentAt < previous.sentAt) return;
        String title = clean(rawTitle);
        String artist = clean(rawArtist);
        // -1 supports embedded players that have not published a playback state yet.
        if (state != 3 && state != -1) title = "";
        String song = title.isEmpty() ? "" : title + (artist.isEmpty() ? "" : " · " + artist);
        latest = new Snapshot(song, state, sentAt, hooks);
    }

    /** "NMUS1\nsentAt\nstate\nhooks\ntitle\nartist" (title / artist without newlines). */
    static void acceptDatagram(byte[] data, int length) {
        String[] f = new String(data, 0, length, StandardCharsets.UTF_8).split("\n", -1);
        if (f.length < 6 || !"NMUS1".equals(f[0])) return;
        try {
            accept(f[4], f[5], Integer.parseInt(f[2]), Long.parseLong(f[1]), Integer.parseInt(f[3]));
        } catch (NumberFormatException ignored) {
            // not ours
        }
    }

    private static synchronized void openLocal() {
        if (localSocket != null) return;
        try {
            DatagramSocket socket = new DatagramSocket(null);
            socket.setReuseAddress(true);
            socket.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), LOCAL_PORT));
            localSocket = socket;
        } catch (Exception error) {
            return;  // the broadcast path still works where Android allows it
        }
        final DatagramSocket socket = localSocket;
        Thread reader = new Thread(() -> {
            byte[] buffer = new byte[2048];
            while (!socket.isClosed()) {
                try {
                    DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
                    socket.receive(packet);
                    acceptDatagram(packet.getData(), packet.getLength());
                } catch (Exception error) {
                    if (socket.isClosed()) return;
                }
            }
        }, "HudNaverMusicLocal");
        reader.setDaemon(true);
        reader.start();
    }

    private static synchronized void closeLocal() {
        if (localSocket == null) return;
        localSocket.close();
        localSocket = null;
    }

    void start() {
        if (registered) return;
        IntentFilter filter = new IntentFilter(UPDATE);
        if (Build.VERSION.SDK_INT >= 33) {
            context.registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED);
        } else {
            context.registerReceiver(receiver, filter);
        }
        registered = true;
        activeReceivers++;
        openLocal();
        // The player may already be running when the HUD starts/restarts.
        context.sendBroadcast(new Intent(REQUEST).setPackage(NAVER));
    }

    void stop() {
        if (!registered) return;
        context.unregisterReceiver(receiver);
        registered = false;
        activeReceivers--;
        if (activeReceivers == 0) {
            closeLocal();
            latest = null;
        }
    }

    static String nowPlaying() {
        Snapshot song = latest;
        boolean publicPlaying = HudMusicService.sessionPlaying;
        String publicSong = publicPlaying ? HudMusicService.nowPlaying : "";
        String publicPackage = HudMusicService.sessionPackage;
        if (song == null || SystemClock.elapsedRealtime() - song.sentAt > 35000L
                || song.song.isEmpty()) return publicSong;
        // An actively playing standalone Bugs/Spotify session wins over old Naver metadata.
        if (publicPlaying && !publicSong.isEmpty() && !NAVER.equals(publicPackage)) return publicSong;
        return song.song;
    }

    static String status() {
        Snapshot song = latest;
        if (song == null || SystemClock.elapsedRealtime() - song.sentAt > 35000L) {
            return "네이버 음악: 모듈 연결 대기";
        }
        if (song.hooks == 0) return "네이버 음악: 모듈 연결됨 · 음악 정보 수집 오류";
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
