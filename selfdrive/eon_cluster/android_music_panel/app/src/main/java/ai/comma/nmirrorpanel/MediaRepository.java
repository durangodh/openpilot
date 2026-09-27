package ai.comma.nmirrorpanel;

import android.app.NotificationManager;
import android.content.ComponentName;
import android.content.Context;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.os.Handler;
import android.os.Looper;
import java.util.ArrayList;
import java.util.List;

/** Observes media sessions only while the panel is visible. */
final class MediaRepository {
    interface Listener { void changed(MediaController controller, boolean authorized); }
    private final Context context;
    private final MediaSessionManager manager;
    private final ComponentName component;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Listener listener;
    private final List<MediaController> controllers = new ArrayList<>();
    private boolean started;
    private String preferred = "";
    private final MediaSessionManager.OnActiveSessionsChangedListener sessions = this::replace;
    private final MediaController.Callback callback = new MediaController.Callback() {
        @Override public void onMetadataChanged(MediaMetadata metadata) { emit(); }
        @Override public void onPlaybackStateChanged(PlaybackState state) { emit(); }
        @Override public void onSessionDestroyed() { handler.post(() -> { if (started) refresh(); }); }
    };
    MediaRepository(Context context, Listener listener) {
        this.context = context;
        this.listener = listener;
        manager = context.getSystemService(MediaSessionManager.class);
        component = new ComponentName(context, MediaAccessService.class);
    }
    boolean authorized() {
        return context.getSystemService(NotificationManager.class).isNotificationListenerAccessGranted(component);
    }
    void start(String preferred) {
        this.preferred = preferred;
        stop();
        started = true;
        if (!authorized()) { listener.changed(null, false); return; }
        try {
            manager.addOnActiveSessionsChangedListener(sessions, component, handler);
            refresh();
        } catch (SecurityException e) { listener.changed(null, false); }
    }
    void stop() {
        started = false;
        manager.removeOnActiveSessionsChangedListener(sessions);
        for (MediaController c : controllers) c.unregisterCallback(callback);
        controllers.clear();
    }
    private void refresh() {
        try { replace(manager.getActiveSessions(component)); }
        catch (SecurityException e) { replace(null); }
    }
    private void replace(List<MediaController> next) {
        if (!started) return;
        for (MediaController c : controllers) c.unregisterCallback(callback);
        controllers.clear();
        if (next != null) for (MediaController c : next) {
            controllers.add(c);
            c.registerCallback(callback, handler);
        }
        emit();
    }
    private void emit() {
        if (!started) return;
        MediaController best = null;
        int bestScore = -1;
        for (MediaController c : controllers) {
            PlaybackState state = c.getPlaybackState();
            MediaMetadata metadata = c.getMetadata();
            if (!MediaInfo.eligible(c.getPackageName(), metadata, state, preferred)) continue;
            int score = PlaybackMath.score(state == null ? 0 : state.getState(),
                    MediaInfo.title(metadata).length() > 0);
            if (!preferred.isEmpty() && preferred.equals(c.getPackageName())) score += 1000;
            if (score > bestScore) { bestScore = score; best = c; }
        }
        listener.changed(best, authorized());
    }
    List<String> activePackages() {
        List<String> packages = new ArrayList<>();
        for (MediaController c : controllers) if (!packages.contains(c.getPackageName())) packages.add(c.getPackageName());
        return packages;
    }
    String diagnostics() {
        StringBuilder result = new StringBuilder("알림 접근: ").append(authorized() ? "허용됨" : "허용 필요");
        result.append("\n감지된 재생 연결: ").append(controllers.size());
        for (MediaController c : controllers) {
            String name = c.getPackageName();
            try { name = context.getPackageManager().getApplicationLabel(context.getPackageManager().getApplicationInfo(name, 0)).toString(); }
            catch (android.content.pm.PackageManager.NameNotFoundException ignored) { }
            MediaMetadata m = c.getMetadata();
            PlaybackState s = c.getPlaybackState();
            result.append("\n\n").append(name);
            result.append("\n곡명: ").append(MediaInfo.title(m).length() > 0 ? "전달됨" : "없음");
            result.append(" / 가수: ").append(MediaInfo.artist(m).length() > 0 ? "전달됨" : "없음");
            result.append("\n재생 상태: ").append(s == null ? "없음" : s.getState() == PlaybackState.STATE_PLAYING ? "재생 중" : "대기/일시정지 등");
            result.append("\n패널 표시 후보: ").append(MediaInfo.eligible(c.getPackageName(),m,s,preferred) ? "포함" : "음악 정보 부족으로 제외");
        }
        result.append("\n\n네이버 연동 재생에 곡 정보가 없으면 벅스 앱에서 직접 재생한 결과와 비교해 주세요.");
        return result.toString();
    }
}
