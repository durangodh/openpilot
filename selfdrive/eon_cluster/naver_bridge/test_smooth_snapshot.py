from build_smooth_snapshot import patch_bridge, patch_snapshot


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
print("PASS smooth snapshot patch")
