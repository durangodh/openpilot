"""JVM checks for boot/display transitions without native Android map dependencies."""
import argparse
from pathlib import Path
import subprocess

from build_marker_size import compile_java

SOURCES = {
  "android/util/DisplayMetrics.java": """package android.util;
public class DisplayMetrics { public float density = 3f; }""",
  "android/content/res/Resources.java": """package android.content.res;
public class Resources {
  public android.util.DisplayMetrics metrics = new android.util.DisplayMetrics();
  public android.util.DisplayMetrics getDisplayMetrics() { return metrics; }
}""",
  "android/content/Context.java": """package android.content;
public class Context {
  public android.content.res.Resources resources = new android.content.res.Resources();
  public android.content.res.Resources getResources() { return resources; }
}""",
  "com/naver/maps/map/overlay/OverlayImage.java": """package com.naver.maps.map.overlay;
public class OverlayImage {
  public int width, height;
  public OverlayImage(int w, int h) { width=w; height=h; }
  public int j(android.content.Context c) { return width; }
  public int i(android.content.Context c) { return height; }
  public static class ResourceDescriptor extends OverlayImage {
    public ResourceDescriptor(int w, int h) { super(w,h); }
    public int j(android.content.Context c) { return Math.round(width*c.getResources().getDisplayMetrics().density); }
    public int i(android.content.Context c) { return Math.round(height*c.getResources().getDisplayMetrics().density); }
  }
}""",
  "com/naver/maps/map/overlay/LocationOverlay.java": """package com.naver.maps.map.overlay;
public class LocationOverlay {
  public OverlayImage icon;
  public int width, height, writes;
  public boolean released;
  public OverlayImage getIcon() { if (released) throw new IllegalStateException(); return icon; }
  public int getIconWidth() { return width; }
  public int getIconHeight() { return height; }
  public void setIconWidth(int w) { width=w; writes++; }
  public void setIconHeight(int h) { height=h; writes++; }
}""",
  "com/naver/map/carrot/CarrotMarkerSizeCheck.java": """package com.naver.map.carrot;
import android.content.Context;
import com.naver.maps.map.overlay.*;
public class CarrotMarkerSizeCheck {
  private static int checks;
  static void check(boolean b, String label) { checks++; if (!b) throw new AssertionError(label); }
  static void size(LocationOverlay o, int w, int h, String label) {
    check(o.width==w && o.height==h, label + ": " + o.width + "x" + o.height);
  }
  public static void main(String[] args) {
    Context c = new Context();
    LocationOverlay o = new LocationOverlay();
    o.icon = new OverlayImage(180,120);
    CarrotMarkerSize.remember(o.icon,3f);
    CarrotMarkerSize.apply(o,c);
    size(o,180,120,"original size at load density");
    int writes=o.writes;
    CarrotMarkerSize.apply(o,c);
    check(o.writes==writes,"no repeated native size writes");
    c.resources.metrics.density=1f;
    CarrotMarkerSize.apply(o,c);
    size(o,60,40,"phone to virtual display");
    c.resources.metrics.density=3f;
    CarrotMarkerSize.apply(o,c);
    size(o,180,120,"virtual display to phone, no compounded scale");
    c.resources.metrics.density=1.5f;
    CarrotMarkerSize.apply(o,c);
    size(o,90,60,"fractional density");
    LocationOverlay other = new LocationOverlay();
    other.icon=o.icon;
    Context otherContext=new Context();
    CarrotMarkerSize.apply(other,otherContext);
    size(other,180,120,"independent simultaneous map");
    size(o,90,60,"other map not modified");
    o.icon=new OverlayImage(240,180);
    CarrotMarkerSize.remember(o.icon,3f);
    CarrotMarkerSize.apply(o,c);
    size(o,120,90,"new night or avatar image");
    o.icon=new OverlayImage.ResourceDescriptor(48,32);
    CarrotMarkerSize.apply(o,c);
    size(o,72,48,"fallback resource current dimensions");
    c.resources.metrics.density=1f;
    CarrotMarkerSize.apply(o,c);
    size(o,48,32,"fallback follows changed resources");
    o.icon=new OverlayImage(200,100);
    CarrotMarkerSize.apply(o,c);
    size(o,0,0,"untracked replacement restores SDK automatic size");
    o.width=17; o.height=23;
    CarrotMarkerSize.apply(o,c);
    size(o,17,23,"untracked original stays untouched");
    CarrotMarkerSize.remember(o.icon,Float.NaN);
    CarrotMarkerSize.remember(new Object(),3f);
    CarrotMarkerSize.remember(null,3f);
    CarrotMarkerSize.apply(o,c);
    size(o,17,23,"invalid source ignored");
    CarrotMarkerSize.remember(o.icon,2f);
    c.resources.metrics.density=0f;
    CarrotMarkerSize.apply(o,c);
    size(o,17,23,"transient zero density ignored");
    c.resources.metrics.density=Float.NaN;
    CarrotMarkerSize.apply(o,c);
    size(o,17,23,"NaN density ignored");
    c.resources.metrics.density=1f;
    CarrotMarkerSize.apply(o,c);
    size(o,100,50,"recovery from invalid metrics");
    writes=o.writes;
    o.icon=null;
    CarrotMarkerSize.apply(o,c);
    o.released=true;
    CarrotMarkerSize.apply(o,c);
    CarrotMarkerSize.apply(null,c);
    CarrotMarkerSize.apply(o,null);
    check(o.writes==writes,"null or released renderer is harmless");
    check(CarrotMarkerSize.pixels(1,3,1)==1,"positive lower bound");
    check(CarrotMarkerSize.pixels(10,Float.POSITIVE_INFINITY,1)==0,"invalid source scale");
    check(CarrotMarkerSize.pixels(0,3,1)==0,"empty image");
    System.out.println("PASS: " + checks + " marker-size checks; device rendering not tested.");
  }
}""",
}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--java-home", type=Path, required=True)
  parser.add_argument("--work", type=Path, required=True)
  parser.add_argument("--ecj", type=Path)
  args = parser.parse_args()
  args.work.mkdir(parents=True, exist_ok=False)
  sources = []
  for name, text in SOURCES.items():
    path = args.work / "src" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    sources.append(path)
  classes = args.work / "classes"
  classes.mkdir()
  compile_java(args.java_home, sources + [Path(__file__).with_name("CarrotMarkerSize.java")], classes, ecj=args.ecj)
  java = args.java_home / ("bin/java.exe" if (args.java_home / "bin/java.exe").exists() else "bin/java")
  subprocess.run([str(java), "-cp", str(classes), "com.naver.map.carrot.CarrotMarkerSizeCheck"], check=True)


if __name__ == "__main__":
  main()
