# CarrotNaver HUD13.9 smooth snapshot

HUD13.9 keeps the stable HUD13.7 feature set and changes only the HUD map
snapshot timing. The bridge requests a map frame every 200 ms instead of every
500 ms, allowing up to 5 fps when Naver's renderer responds normally. A stalled
`NaverMap.takeSnapshot()` request is retried after 1.2 s instead of 3 s.

The map remains latest-frame based on EON and Remote HUD; this change does not
alter navigation guidance, GPS injection, map geometry, or vehicle controls.
The build verifies that only `classes43.dex` differs from HUD13.7 and signs all
splits with the same official HUD certificate so it can update in place.
