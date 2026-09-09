# CarrotNaver HUD14

HUD14 keeps HUD13's verified `NaverMap.takeSnapshot()` capture and changes the
transport between the Naver app and EON. Each captured map is JPEG encoded at
quality 65 and sent as a raw binary WebSocket frame. This matches TMAP's map
transport profile and removes the large Base64 JSON conversion that could fail
silently inside HUD13's `sendBitmap()`.

EON accepts the binary frame on Naver's existing
`/api/navi/ws/v2/json/naver/state` connection. HUD3-HUD13 Base64 map messages
remain supported.

The Naver log confirms the complete sender path with:

```text
CarrotMapBinary: first binary map_main ... bytes
```

If encoding or socket transmission fails, a throttled
`CarrotMapBinary: binary map_main failed: ...` line records the actual error.
