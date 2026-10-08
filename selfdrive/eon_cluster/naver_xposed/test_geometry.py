#!/usr/bin/env python3
"""Compile the Android-free NAVER HUD geometry helper and verify 12.3-inch framing."""

import pathlib
import shutil
import subprocess
import tempfile


ROOT = pathlib.Path(__file__).resolve().parent
SOURCE = ROOT / "app/src/main/java/ai/comma/naverhud/NaverMapGeometry.java"


CHECK = r"""
package ai.comma.naverhud;
public final class NaverMapGeometryCheck {
  private static void check(boolean value, String message) {
    if (!value) throw new AssertionError(message);
  }
  public static void main(String[] args) {
    check(NaverMapGeometry.PANEL_WIDTH == 760, "panel width");
    check(NaverMapGeometry.PANEL_HEIGHT == 720, "panel height");
    check(NaverMapGeometry.MAP_TOP == 132, "turn-banner reserve");
    check(NaverMapGeometry.MAP_BOTTOM == 662, "ETA reserve");

    int[] landscape = NaverMapGeometry.sourceCrop(1034, 720);
    check(landscape[0] == 1 && landscape[1] == 0, "landscape origin");
    check(landscape[2] == 1033 && landscape[3] == 720, "landscape keeps full width");
    float scale = 760f / (landscape[2] - landscape[0]);
    check(scale > 0.73f && scale < 0.74f, "old visual scale restored");

    int[] wide = NaverMapGeometry.sourceCrop(1920, 1080);
    check(wide[1] == 0 && wide[3] == 1080, "wide source keeps full height");
    check(wide[2] - wide[0] == 1549, "wide source crops symmetrically");

    int[] portrait = NaverMapGeometry.sourceCrop(720, 1280);
    check(portrait[0] == 0 && portrait[2] == 720, "portrait keeps width");
    check(portrait[3] - portrait[1] == 502, "portrait target aspect");
    check(portrait[1] > 1280 / 2 - 251, "portrait follows lower vehicle position");
  }
}
"""


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="naver-geometry-") as directory:
        work = pathlib.Path(directory)
        check = work / "NaverMapGeometryCheck.java"
        javac = shutil.which("javac")
        if javac:
            check.write_text(CHECK, encoding="utf-8")
            subprocess.run([javac, "-d", str(work), str(SOURCE), str(check)], check=True)
            subprocess.run(["java", "-cp", str(work), "ai.comma.naverhud.NaverMapGeometryCheck"], check=True)
        else:
            # The Codex runtime ships the Java source launcher but not the javac binary.
            # Put the package-private helper and check in one compilation unit.
            helper = SOURCE.read_text(encoding="utf-8").replace(
                "package ai.comma.naverhud;\n", "", 1
            )
            check_body = CHECK.replace("package ai.comma.naverhud;\n", "", 1)
            check.write_text(
                "package ai.comma.naverhud;\n" + check_body + "\n" + helper,
                encoding="utf-8",
            )
            subprocess.run(["java", "--source", "11", str(check)], check=True)


if __name__ == "__main__":
    main()
