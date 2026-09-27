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
            String p = c.getPackageName();
            if (p.equals("com.nhn.android.nmap") || p.equals("com.skt.tmap.ku")) continue;
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
            int score = PlaybackMath.score(state == null ? 0 : state.getState(),
                    metadata != null && metadata.getText(MediaMetadata.METADATA_KEY_TITLE) != null);
            if (!preferred.isEmpty() && preferred.equals(c.getPackageName())) score += 1000;
            if (score > bestScore) { bestScore = score; best = c; }
        }
        listener.changed(best, authorized());
    }
}
