"""HUD12 snapshot-path harness: fake Android + fake obfuscated NaverMap (p2 = takeSnapshot,
SnapshotReadyCallback.a) + fake MapProvider.i(). Checks ownership handoff, request pacing,
timeout re-request, 960x576 center-crop, dead-renderer release and recovery.

Usage: python test_car_snapshot.py --java-home /path/to/jdk --work /new/scratch/dir
"""
import argparse
from pathlib import Path
import subprocess


SOURCES = {
  'android/content/Context.java': 'package android.content; public class Context { public java.io.File getExternalFilesDir(String s){return new java.io.File("/tmp");} public java.io.File getFilesDir(){return new java.io.File("/tmp");} }\n',
  'android/graphics/Bitmap.java': 'package android.graphics;\npublic class Bitmap { public enum Config{ARGB_8888} public int w,h; boolean rec; public Bitmap(int w,int h){this.w=w;this.h=h;}\n public static Bitmap createBitmap(int w,int h,Config c){return new Bitmap(w,h);} public int getWidth(){return w;} public int getHeight(){return h;}\n public boolean isRecycled(){return rec;} public void recycle(){rec=true;} public Bitmap copy(Config c,boolean m){return new Bitmap(w,h);} }\n',
  'android/graphics/Canvas.java': 'package android.graphics;\npublic class Canvas { public static Rect lastSrc; public Canvas(Bitmap b){} public void drawBitmap(Bitmap b,Rect s,Rect d,Paint p){lastSrc=s;} }\n',
  'android/graphics/Paint.java': 'package android.graphics; public class Paint { public static final int FILTER_BITMAP_FLAG=2; public Paint(int f){} }\n',
  'android/graphics/Rect.java': 'package android.graphics; public class Rect { public int left,top,right,bottom; public Rect(int l,int t,int r,int b){left=l;top=t;right=r;bottom=b;} }\n',
  'android/os/Handler.java': 'package android.os; public class Handler { public static java.util.List<Runnable> q=new java.util.ArrayList<>(); public Handler(Looper l){} public boolean post(Runnable r){q.add(r);return true;} public static void drain(){while(!q.isEmpty())q.remove(0).run();} }\n',
  'android/os/Looper.java': 'package android.os; public class Looper { public static Looper getMainLooper(){return new Looper();} }\n',
  'android/os/SystemClock.java': 'package android.os; public class SystemClock { public static long now=1000; public static long elapsedRealtime(){return now;} }\n',
  'android/util/Log.java': 'package android.util; public class Log { public static int i(String t,String m){System.out.println(t+": "+m);return 0;} }\n',
  'com/naver/map/carrot/CarrotNaverBridge.java': 'package com.naver.map.carrot; class CarrotNaverBridge { public int sent; public android.graphics.Bitmap last; void sendBitmap(Object b){sent++;last=(android.graphics.Bitmap)b;} }\n',
  'com/naver/map/carrot/FakeProvider.java': 'package com.naver.map.carrot; public class FakeProvider { public com.naver.maps.map.NaverMap map; public com.naver.maps.map.NaverMap i(){return map;} }\n',
  'com/naver/map/carrot/SnapCheck.java': 'package com.naver.map.carrot; import android.os.*;\npublic class SnapCheck { static void c(boolean ok,String w){if(!ok)throw new AssertionError(w);}\n public static void main(String[] a){\n  CarrotNaverBridge b=new CarrotNaverBridge(); FakeProvider p=new FakeProvider();\n  c(!CarrotCarMapSnapshot.capture(b),"no provider -> phone capture");\n  CarrotCarMapSnapshot.provider(p);\n  c(!CarrotCarMapSnapshot.capture(b),"map not ready -> phone capture");\n  p.map=new com.naver.maps.map.NaverMap();\n  c(CarrotCarMapSnapshot.capture(b),"map ready -> owns map_main"); Handler.drain();\n  c(p.map.requests==1,"snapshot requested");\n  SystemClock.now+=500; CarrotCarMapSnapshot.capture(b); Handler.drain(); c(p.map.requests==1,"no overlapping request while pending");\n  p.map.cb.a(new android.graphics.Bitmap(1024,600));\n  c(b.sent==1 && b.last.w==960 && b.last.h==576,"scaled to 960x576");\n  android.graphics.Rect s=android.graphics.Canvas.lastSrc; c(s.top==0 && s.bottom==600 && s.left==12 && s.right==1012,"center crop 1024x600 -> 1000x600 "+s.left+","+s.right);\n  SystemClock.now+=500; c(CarrotCarMapSnapshot.capture(b),"next tick"); Handler.drain(); c(p.map.requests==2,"second request after callback");\n  SystemClock.now+=3500; CarrotCarMapSnapshot.capture(b); Handler.drain(); c(p.map.requests==3,"timeout re-request");\n  SystemClock.now+=13000; c(!CarrotCarMapSnapshot.capture(b),"dead renderer -> release map_main");\n  p.map.cb.a(new android.graphics.Bitmap(800,480)); SystemClock.now+=500; c(CarrotCarMapSnapshot.capture(b),"recovers after a bitmap");\n  System.out.println("PASS snapshot path");\n } }\n',
  'com/naver/maps/map/NaverMap.java': 'package com.naver.maps.map;\npublic class NaverMap { public interface SnapshotReadyCallback { void a(android.graphics.Bitmap b); }\n public SnapshotReadyCallback cb; public int requests; public void p2(boolean z, SnapshotReadyCallback c){cb=c;requests++;} }\n',
}


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--java-home", type=Path, required=True)
  parser.add_argument("--work", type=Path, required=True)
  args = parser.parse_args()
  work = args.work.resolve()
  work.mkdir(parents=True, exist_ok=False)
  src = work / "src"
  for name, text in SOURCES.items():
    path = src / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
  classes = work / "classes"
  classes.mkdir()
  suffix = ".exe" if (args.java_home / "bin/java.exe").exists() else ""
  here = Path(__file__).resolve().parent
  files = [str(p) for p in src.rglob("*.java")] + [str(here / "CarrotCarMapSnapshot.java"), str(here / "CarrotHudLog.java")]
  subprocess.run([str(args.java_home / ("bin/javac" + suffix)), "--release", "8", "-encoding", "UTF-8",
                  "-Xlint:-options", "-d", str(classes)] + files, check=True)
  subprocess.run([str(args.java_home / ("bin/java" + suffix)), "-cp", str(classes), "com.naver.map.carrot.SnapCheck"], check=True)


if __name__ == "__main__":
  main()
