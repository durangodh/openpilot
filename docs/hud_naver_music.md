# Embedded Naver/Bugs music on the HUD

The Remote HUD app can already read standalone Bugs/Spotify through the phone's
notification-listener permission and public active media sessions. Playing Bugs
inside Naver Map did not appear for the owner even though standalone playback did.

The Naver LSPosed module now observes `MediaSession.setMetadata`,
`setPlaybackState`, and `release`, and their `MediaSessionCompat` counterparts,
inside Naver's main process. It forwards music metadata directly to Remote HUD on
the same phone, including sessions that were not made publicly active. These are
read-only hooks; they do not start playback, inject buttons, or change navigation.
An artist, album, or positive duration is required to distinguish music from
spoken navigation. Stopped, cleared, errored, and released sessions are hidden.
Remote HUD hides paused playback too, including paused snapshots from older Naver
modules. Metadata can remain cached for resume without keeping the HUD badge visible.

Remote HUD requests the current song when its service starts. The module also
sends a snapshot every 10 seconds so either app can start first. Old snapshots
expire after 35 seconds if the module exits. Active standalone playback takes
precedence over an old embedded-player title.

## Install and check

1. Update both **EON Remote HUD** (`remote-hud-auto`) and **Naver HUD Xposed
   module** (`naver-hud-auto`), retaining the existing release signing keys.
2. Keep the Naver module enabled for `com.nhn.android.nmap` in LSPosed. Completely
   stop and reopen Naver Map after updating the module, or reboot the phone.
3. Start HUD output and play music inside Naver's Bugs player.
4. The song should use the existing music icon and date/time placement. In Remote
   HUD's **HUD 음악 정보** card, check the new Naver music status:
   - `모듈 연결 대기`: the new Naver module has not sent a recent snapshot.
   - `모듈 연결됨 · 곡 정보 대기`: the module is running, but these metadata APIs
     have not supplied a music title.
   - `수신 중`: the song reached Remote HUD; the title is included in this status.

Naver's internal Bugs implementation has not been inspected from the installed
APK or tested on the owner's phone. This bridge covers framework/compat metadata
publication; a custom player that never uses either API needs an additional
player-specific hook. The status makes that distinction visible without guessing
from a missing title. The original NAVER-signed map APK is not changed.

## Checks

`MusicSessionStateCheck` checks metadata before playback state, playback before
metadata, speech exclusion, track changes, competing paused/playing sessions,
stop/restart, cleared metadata, release, errors, and bounded Unicode text. Both
APK workflows run it before packaging. Device playback and LSPosed hook delivery
still require a phone test.
