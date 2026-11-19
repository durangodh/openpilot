package ai.comma.hudcommon;

/** No Android runtime needed: ordering and lifetime of embedded music sessions. */
public final class MusicSessionStateCheck {
    private static void expect(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    public static void main(String[] args) {
        MusicSessionState state = new MusicSessionState();
        Object music = new Object();
        Object speech = new Object();
        Object other = new Object();
        state.metadata(music, "집에 돌아오는 길", "AKMU(악뮤)", "", 0);
        expect(state.current().title.equals("집에 돌아오는 길"),
                "Unpublished session without playback state must still supply metadata");
        state.playback(music, 3);
        state.metadata(speech, "300미터 앞 우회전", "", "", 0);
        state.playback(speech, 3);
        expect(state.current().artist.equals("AKMU(악뮤)"), "Guidance must not replace music");
        state.playback(other, 2);
        state.metadata(other, "이전 곡", "다른 가수", "", 0);
        expect(state.current().title.equals("집에 돌아오는 길"), "Paused session loses to playing music");
        state.metadata(music, "다음 곡", "다음 가수", "", 0);
        expect(state.current().title.equals("다음 곡"), "Track changes update immediately");
        state.playback(music, 1);
        expect(state.current().title.equals("이전 곡"), "Stopped music is excluded");
        state.release(other);
        expect(state.current().title.isEmpty(), "Speech and stopped sessions leave no title");
        state.playback(music, 3);
        expect(state.current().title.equals("다음 곡"), "Play after stop can reuse metadata");
        state.metadata(music, null, null, null, 0);
        expect(state.current().title.isEmpty(), "Clearing metadata removes stale music");
        state.release(music);
        state.playback(other, 3);
        state.metadata(other, "  제목\n두줄  ", " 가수\r이름 ", "", 0);
        expect(state.current().title.equals("제목 두줄"), "Normalize incoming text");
        expect(state.current().artist.equals("가수 이름"), "Normalize artist");
        String emoji = new String(Character.toChars(0x1f3b5));
        String longTitle = emoji.repeat(210);
        String bounded = MusicSessionState.clean(longTitle);
        expect(bounded.codePointCount(0, bounded.length()) == 200, "Bound payload without splitting emoji");
        state.release(other);
        state.metadata(other, "연주곡", "", "앨범", 0);
        expect(state.current().title.equals("연주곡"), "Album evidence allows artist-less tracks");
        state.playback(other, 7);
        expect(state.current().title.isEmpty(), "Error state clears music");
        System.out.println("MusicSessionStateCheck passed");
    }
}
