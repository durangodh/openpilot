package ai.comma.naverhud;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.media.MediaMetadata;
import android.media.session.MediaSession;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;

import ai.comma.hudcommon.MusicSessionState;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;

/** Read-only hooks: forward embedded playback without needing a public active session. */
final class NaverMusic {
    private static final String UPDATE = "ai.comma.remotehud.NAVER_MUSIC";
    private static final String REQUEST = "ai.comma.remotehud.NAVER_MUSIC_REQUEST";
    private static final String HUD = "ai.comma.remotehud";
    private static NaverMusic instance;
    private final Context context;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final MusicSessionState state = new MusicSessionState();
    private String lastTitle = "";
    private String lastArtist = "";
    private int lastState;
    private int metadataHooks;
    private boolean hookFailed;

    private NaverMusic(Context context) { this.context = context; }

    static synchronized void install(Context context, ClassLoader loader) {
        if (instance != null) return;
        NaverMusic music = new NaverMusic(context);
        IntentFilter filter = new IntentFilter(REQUEST);
        BroadcastReceiver requests = new BroadcastReceiver() {
            @Override public void onReceive(Context ignored, Intent intent) {
                if (REQUEST.equals(intent.getAction())) music.publish(true);
            }
        };
        if (Build.VERSION.SDK_INT >= 33) {
            context.registerReceiver(requests, filter, Context.RECEIVER_EXPORTED);
        } else {
            context.registerReceiver(requests, filter);
        }
        instance = music;
        handlerStart(music);
        // Transport remains alive even if either metadata hook cannot be installed.
        try {
            music.metadataHooks += music.hook(MediaSession.class);
        } catch (Throwable error) {
            music.hookFailed = true;
            NaverHudLog.line("music framework hook failed: " + error);
        }
        try {
            Class<?> compat = XposedHelpers.findClassIfExists(
                    "android.support.v4.media.session.MediaSessionCompat", loader);
            if (compat != null) music.metadataHooks += music.hook(compat);
        } catch (Throwable error) {
            music.hookFailed = true;
            NaverHudLog.line("music compat hook failed: " + error);
        }
        music.handler.post(() -> music.publish(true));
        NaverHudLog.line("music bridge installed hooks=" + music.metadataHooks
                + " hookFailed=" + music.hookFailed);
    }

    private static void handlerStart(NaverMusic music) {
        music.handler.post(new Runnable() {
            @Override public void run() {
                music.publish(true);
                music.handler.postDelayed(this, 10000L);
            }
        });
    }

    private int hook(Class<?> session) {
        int count = XposedBridge.hookAllMethods(session, "setMetadata", new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                if (param.hasThrowable() || param.args.length != 1) return;
                try {
                    Object metadata = param.args[0];
                    state.metadata(param.thisObject,
                            text(metadata, MediaMetadata.METADATA_KEY_TITLE,
                                    MediaMetadata.METADATA_KEY_DISPLAY_TITLE),
                            text(metadata, MediaMetadata.METADATA_KEY_ARTIST,
                                    MediaMetadata.METADATA_KEY_ALBUM_ARTIST,
                                    MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE),
                            text(metadata, MediaMetadata.METADATA_KEY_ALBUM),
                            number(metadata, "getLong", MediaMetadata.METADATA_KEY_DURATION));
                    changed();
                } catch (Throwable error) {
                    NaverHudLog.line("music metadata read failed: " + error.getClass().getSimpleName());
                }
            }
        }).size();
        XposedBridge.hookAllMethods(session, "setPlaybackState", new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                if (param.hasThrowable() || param.args.length != 1) return;
                try {
                    Object playback = param.args[0];
                    int value = playback == null ? 0
                            : ((Number) XposedHelpers.callMethod(playback, "getState")).intValue();
                    state.playback(param.thisObject, value);
                    changed();
                } catch (Throwable error) {
                    NaverHudLog.line("music playback read failed: " + error.getClass().getSimpleName());
                }
            }
        });
        XposedBridge.hookAllMethods(session, "release", new XC_MethodHook() {
            @Override protected void afterHookedMethod(MethodHookParam param) {
                if (param.hasThrowable()) return;
                state.release(param.thisObject);
                changed();
            }
        });
        return count;
    }

    private static String text(Object metadata, String... keys) {
        if (metadata == null) return "";
        for (String key : keys) {
            Object value = XposedHelpers.callMethod(metadata, "getText", key);
            String result = MusicSessionState.clean(value == null ? "" : value.toString());
            if (!result.isEmpty()) return result;
        }
        return "";
    }

    private static long number(Object target, String method, String key) {
        if (target == null) return 0;
        Object value = XposedHelpers.callMethod(target, method, key);
        return value instanceof Number ? ((Number) value).longValue() : 0;
    }

    private void changed() {
        // Compat forwards to the framework API synchronously. Coalesce that pair.
        handler.removeCallbacks(sendChanged);
        handler.post(sendChanged);
    }

    private final Runnable sendChanged = () -> publish(false);

    private void publish(boolean force) {
        MusicSessionState.Song song = state.current();
        boolean changed = !song.title.equals(lastTitle) || !song.artist.equals(lastArtist)
                || song.state != lastState;
        if (!force && !changed) return;
        if (changed) {
            NaverHudLog.line("music bridge metadata=" + !song.title.isEmpty() + " state=" + song.state);
        }
        lastTitle = song.title;
        lastArtist = song.artist;
        lastState = song.state;
        try {
            context.sendBroadcast(new Intent(UPDATE).setPackage(HUD)
                    .putExtra("title", song.title).putExtra("artist", song.artist)
                    .putExtra("state", song.state).putExtra("sentAt", SystemClock.elapsedRealtime())
                    .putExtra("hooks", metadataHooks).putExtra("hookFailed", hookFailed));
        } catch (RuntimeException error) {
            NaverHudLog.line("music bridge send failed: " + error.getClass().getSimpleName());
        }
    }
}
