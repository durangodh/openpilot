from pathlib import Path

from build_guidance_text import CALL, ESC, STRING, patch_guidance


fixture = """.method private guidance(Ljava/lang/Object;)Ljava/lang/String;
    .locals 7

    const-string v3, "v"

    invoke-static {v0, v3}, %s

    move-result-object v0

    invoke-static {v0}, %s

    move-result-object v0

    const-string v1, ",\\\"main_text\\\":\\\""

    invoke-virtual {p1, v1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    move-result-object p1

    invoke-static {v0}, %s

    move-result-object v1

    const-string v1, "\\\",\\\"road_name\\\":\\\""

    invoke-static {v0}, %s
.end method
""" % (CALL, STRING, ESC, ESC)

patched = patch_guidance(fixture)
assert ".locals 8" in patched and ".locals 7" not in patched
assert patched.count('const-string v3, "n"') == 1
assert patched.count('const-string v3, "v"') == 1
assert patched.count(":carrot_guidance_text_ok") == 2
# main_text uses v7 (display text), road_name still uses v0 (roadName).
main_at = patched.index("main_text")
road_at = patched.index("road_name")
assert "invoke-static {v7}, " + ESC in patched[main_at:road_at]
assert "invoke-static {v0}, " + ESC in patched[road_at:]
print("PASS guidance text patch")
