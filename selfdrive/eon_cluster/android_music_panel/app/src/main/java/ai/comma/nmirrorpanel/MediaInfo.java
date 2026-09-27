package ai.comma.nmirrorpanel;

import android.media.MediaMetadata;
import android.media.session.PlaybackState;

/** Metadata shared by selection and rendering, including display-only players. */
final class MediaInfo {
    private static CharSequence first(MediaMetadata metadata, String... keys) {
        if (metadata != null) for (String key : keys) {
            CharSequence value = metadata.getText(key);
            if (value != null && !value.toString().trim().isEmpty()) return value;
        }
        return "";
    }
    static CharSequence title(MediaMetadata metadata) {
        return first(metadata, MediaMetadata.METADATA_KEY_TITLE, MediaMetadata.METADATA_KEY_DISPLAY_TITLE);
    }
    static CharSequence artist(MediaMetadata metadata) {
        return first(metadata, MediaMetadata.METADATA_KEY_ARTIST, MediaMetadata.METADATA_KEY_ALBUM_ARTIST,
                MediaMetadata.METADATA_KEY_DISPLAY_SUBTITLE);
    }
    static boolean eligible(String packageName, MediaMetadata metadata, PlaybackState state, String preferred) {
        boolean navigation = packageName.equals("com.nhn.android.nmap") || packageName.equals("com.skt.tmap.ku");
        if (!navigation) return true;
        // A navigation package can also own integrated music. Keep observing it
        // even while it has no song, so later metadata callbacks can select it.
        if (title(metadata).length() == 0) return false;
        if (packageName.equals(preferred)) return true;
        long actions = state == null ? 0 : state.getActions();
        return artist(metadata).length() > 0
                || first(metadata, MediaMetadata.METADATA_KEY_ALBUM).length() > 0
                || (metadata != null && metadata.getLong(MediaMetadata.METADATA_KEY_DURATION) > 0)
                || (actions & (PlaybackState.ACTION_SKIP_TO_NEXT | PlaybackState.ACTION_SKIP_TO_PREVIOUS)) != 0;
    }
    private MediaInfo() { }
}
