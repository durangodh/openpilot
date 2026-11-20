package ai.comma.remotehud;

import android.content.ComponentName;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.os.Handler;
import android.os.Looper;
import android.service.notification.NotificationListenerService;
import java.util.ArrayList;
import java.util.List;

/** Observes playback metadata on the HUD phone, without reading notification contents. */
public final class HudMusicService extends NotificationListenerService {
    static volatile String nowPlaying = "";
    static volatile boolean sessionPlaying;
    static volatile String sessionPackage = "";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final List<MediaController> controllers = new ArrayList<>();
    private MediaSessionManager manager;
    private boolean connected;
    private final MediaSessionManager.OnActiveSessionsChangedListener sessions = this::replace;
    private final MediaController.Callback callback = new MediaController.Callback() {
        @Override public void onMetadataChanged(MediaMetadata metadata) { updateSong(); }
        @Override public void onPlaybackStateChanged(PlaybackState state) { updateSong(); }
        @Override public void onSessionDestroyed() { handler.post(() -> refresh()); }
    };

    @Override public void onListenerConnected() {
        super.onListenerConnected();
        disconnect();
        manager = getSystemService(MediaSessionManager.class);
        if (manager == null) return;
        connected = true;
        try {
            manager.addOnActiveSessionsChangedListener(sessions,
                    new ComponentName(this, HudMusicService.class), handler);
            refresh();
        } catch (SecurityException error) {
            disconnect();
        }
    }

    private void refresh() {
        if (!connected) return;
        try {
            replace(manager.getActiveSessions(new ComponentName(this, HudMusicService.class)));
        } catch (SecurityException error) {
            disconnect();
        }
    }

    private void replace(List<MediaController> next) {
        if (!connected) return;
        for (MediaController controller : controllers) controller.unregisterCallback(callback);
        controllers.clear();
        if (next != null) {
            for (MediaController controller : next) {
                controllers.add(controller);
                controller.registerCallback(callback, handler);
            }
        }
        updateSong();
    }

    private static String metadataText(MediaMetadata metadata, String... keys) {
        if (metadata == null) return "";
        for (String key : keys) {
            CharSequence value = metadata.getText(key);
            if (value != null && !value.toString().trim().isEmpty()) {
                return value.toString().replace('\n', ' ').replace('\r', ' ').trim();
            }
        }
        return "";
    }

    private void updateSong() {
        if (!connected) return;
        MediaController best = null;
        String song = "";
        for (MediaController controller : controllers) {
            PlaybackState state = controller.getPlaybackState();
            MediaMetadata metadata = controller.getMetadata();
            String title = metadataText(metadata, MediaMetadata.METADATA_KEY_TITLE,
                    MediaMetadata.METADATA_KEY_DISPLAY_TITLE);
            String artist = metadataText(metadata, MediaMetadata.METADATA_KEY_ARTIST,
                    MediaMetadata.METADATA_KEY_ALBUM_ARTIST, MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE);
            if (state == null || title.isEmpty()) continue;
            int playback = state.getState();
            // Paused sessions keep their metadata, but must not keep a HUD title visible.
            if (playback != PlaybackState.STATE_PLAYING) continue;
            // Navigation apps may also host Bugs playback. Ignore spoken guidance sessions.
            if (controller.getPackageName().equals("com.nhn.android.nmap")
                    && artist.isEmpty()
                    && metadataText(metadata, MediaMetadata.METADATA_KEY_ALBUM).isEmpty()
                    && metadata.getLong(MediaMetadata.METADATA_KEY_DURATION) <= 0
                    && (state.getActions() & (PlaybackState.ACTION_SKIP_TO_NEXT
                            | PlaybackState.ACTION_SKIP_TO_PREVIOUS)) == 0) continue;
            best = controller;
            song = title + (artist.isEmpty() ? "" : " · " + artist);
            break;
        }
        nowPlaying = best == null ? "" : song;
        sessionPlaying = best != null;
        sessionPackage = best == null ? "" : best.getPackageName();
    }

    private void disconnect() {
        connected = false;
        if (manager != null) manager.removeOnActiveSessionsChangedListener(sessions);
        for (MediaController controller : controllers) controller.unregisterCallback(callback);
        controllers.clear();
        handler.removeCallbacksAndMessages(null);
        nowPlaying = "";
        sessionPlaying = false;
        sessionPackage = "";
    }

    @Override public void onListenerDisconnected() {
        disconnect();
        super.onListenerDisconnected();
    }

    @Override public void onDestroy() {
        disconnect();
        super.onDestroy();
    }
}
