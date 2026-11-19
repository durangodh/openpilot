package ai.comma.hudcommon;

import java.util.Map;
import java.util.WeakHashMap;

/** In-process music metadata, including sessions never published as active. */
public final class MusicSessionState {
    private final Map<Object, Track> tracks = new WeakHashMap<>();
    private long sequence;

    public static final class Song {
        public final String title;
        public final String artist;
        public final int state;

        Song(String title, String artist, int state) {
            this.title = title;
            this.artist = artist;
            this.state = state;
        }
    }

    private static final class Track {
        String title = "";
        String artist = "";
        int state = -1; // Metadata can arrive before a playback state exists.
        long sequence;
    }

    public synchronized void metadata(Object session, String title, String artist,
            String album, long duration) {
        if (session == null) return;
        Track track = tracks.get(session);
        if (track == null) {
            track = new Track();
            tracks.put(session, track);
        }
        title = clean(title);
        artist = clean(artist);
        // Naver also creates speech/guidance sessions. A title alone isn't music.
        boolean music = !title.isEmpty()
                && (!artist.isEmpty() || !clean(album).isEmpty() || duration > 0);
        track.title = music ? title : "";
        track.artist = music ? artist : "";
        track.sequence = ++sequence;
    }

    public synchronized void playback(Object session, int state) {
        if (session == null) return;
        Track track = tracks.get(session);
        if (track == null) {
            track = new Track();
            tracks.put(session, track);
        }
        track.state = state;
        track.sequence = ++sequence;
    }

    public synchronized void release(Object session) {
        tracks.remove(session);
    }

    public synchronized Song current() {
        Track best = null;
        for (Track track : tracks.values()) {
            if (track.title.isEmpty() || rank(track.state) == 0) continue;
            if (best == null || rank(track.state) > rank(best.state)
                    || (rank(track.state) == rank(best.state) && track.sequence > best.sequence)) {
                best = track;
            }
        }
        return best == null ? new Song("", "", 0)
                : new Song(best.title, best.artist, best.state);
    }

    private static int rank(int state) {
        // PlaybackState: NONE=0, STOPPED=1, PAUSED=2, PLAYING=3, ERROR=7.
        if (state == 0 || state == 1 || state == 7) return 0;
        if (state == 3) return 3;
        if (state == 2) return 1;
        return 2;
    }

    public static String clean(String text) {
        if (text == null) return "";
        text = text.replace('\n', ' ').replace('\r', ' ').trim();
        int count = text.codePointCount(0, text.length());
        return count <= 200 ? text : text.substring(0, text.offsetByCodePoints(0, 200));
    }
}
