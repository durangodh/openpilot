from build_smooth_snapshot import patch_bridge, patch_naver_map, patch_snapshot


bridge = """.method public run()V
    const-wide/16 v4, 0x1f4

    add-long/2addr v2, v4
.end method
"""
patched_bridge = patch_bridge(bridge)
assert "const-wide/16 v4, 0xc8" in patched_bridge
assert "0x1f4" not in patched_bridge

snapshot = """.field private static final SNAPSHOT_TIMEOUT_MS:J = 0xbb8L
.method public static capture(Lx;)Z
    const-wide/16 v10, 0xbb8
.end method
"""
patched_snapshot = patch_snapshot(snapshot)
assert "SNAPSHOT_TIMEOUT_MS:J = 0x4b0L" in patched_snapshot
assert "const-wide/16 v10, 0x4b0" in patched_snapshot
assert "0xbb8" not in patched_snapshot

naver_map = """.method public p2(ZLcom/naver/maps/map/NaverMap$SnapshotReadyCallback;)V
    .locals 0
    iget-object p2, p0, Lcom/naver/maps/map/NaverMap;->b:Lcom/naver/maps/map/NativeMapView;

    .line 4
    .line 5
    invoke-virtual {p2, p1}, Lcom/naver/maps/map/NativeMapView;->f1(Z)V
.end method
"""
patched_naver_map = patch_naver_map(naver_map)
assert patched_naver_map.count("MapRendererScheduler;->requestRender()V") == 1
assert patched_naver_map.count("NativeMapView;->f1(Z)V") == 1
print("PASS smooth snapshot patch")
