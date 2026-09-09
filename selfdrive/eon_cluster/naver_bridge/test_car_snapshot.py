"""HUD12 snapshot-path harness: fake Android + fake obfuscated NaverMap (p2 = takeSnapshot,
SnapshotReadyCallback.a) + fake MapProvider.i(). Checks ownership handoff, request pacing,
timeout re-request, 960x576 center-crop, dead-renderer release and recovery.

Usage: python test_car_snapshot.py --java-home /path/to/jdk --work /new/scratch/dir
"""
import argparse
from pathlib import Path
import subprocess


SOURCES = {
  'android/app/Activity.java': 'package android.app; public class Activity extends android.content.Context { public android.view.Window w=new android.view.Window(); public android.view.WindowManager wm; public boolean fin;\n public Activity(int display){wm=new android.view.WindowManager(display);} public android.view.Window getWindow(){return w;} public android.view.WindowManager getWindowManager(){return wm;} public boolean isFinishing(){return fin;} public boolean isDestroyed(){return fin;} }\n',
  'android/content/Context.java': 'package android.content; public class Context { public java.io.File getExternalFilesDir(String s){return new java.io.File("/tmp");} public java.io.File getFilesDir(){return new java.io.File("/tmp");} }\n',
  'android/graphics/Bitmap.java': 'package android.graphics;\npublic class Bitmap { public enum Config{ARGB_8888} public enum CompressFormat{JPEG,PNG} public boolean compress(CompressFormat f,int q,java.io.OutputStream o){try{o.write(new byte[]{(byte)0xff,(byte)0xd8,1,2,(byte)0xff,(byte)0xd9});}catch(Exception e){} return true;} public int w,h; boolean rec; public Bitmap(int w,int h){this.w=w;this.h=h;}\n public static Bitmap createBitmap(int w,int h,Config c){return new Bitmap(w,h);} public int getWidth(){return w;} public int getHeight(){return h;}\n public boolean isRecycled(){return rec;} public void recycle(){rec=true;} public Bitmap copy(Config c,boolean m){return new Bitmap(w,h);} }\n',
  'android/graphics/Canvas.java': 'package android.graphics;\npublic class Canvas { public static Rect lastSrc; public Canvas(Bitmap b){} public void drawBitmap(Bitmap b,Rect s,Rect d,Paint p){lastSrc=s;} }\n',
  'android/graphics/Paint.java': 'package android.graphics; public class Paint { public static final int FILTER_BITMAP_FLAG=2; public Paint(int f){} }\n',
  'android/graphics/Rect.java': 'package android.graphics; public class Rect { public int left,top,right,bottom; public Rect(int l,int t,int r,int b){left=l;top=t;right=r;bottom=b;} }\n',
  'android/os/Handler.java': 'package android.os; public class Handler { public static java.util.List<Runnable> q=new java.util.ArrayList<>(); public Handler(Looper l){} public boolean post(Runnable r){q.add(r);return true;} public static void drain(){while(!q.isEmpty())q.remove(0).run();} }\n',
  'android/os/HandlerThread.java': 'package android.os; public class HandlerThread { public HandlerThread(String n){} public void start(){} public Looper getLooper(){return Looper.getMainLooper();} }\n',
  'android/os/Looper.java': 'package android.os; public class Looper { public static Looper getMainLooper(){return new Looper();} }\n',
  'android/os/SystemClock.java': 'package android.os; public class SystemClock { public static long now=1000; public static long elapsedRealtime(){return now;} }\n',
  'android/util/Base64.java': 'package android.util; public class Base64 { public static final int NO_WRAP=2; public static String encodeToString(byte[] b,int f){return java.util.Base64.getEncoder().encodeToString(b);} }\n',
  'android/util/Log.java': 'package android.util; public class Log { public static int i(String t,String m){System.out.println(t+": "+m);return 0;} }\n',
  'android/view/Display.java': 'package android.view; public class Display { public int id; public Display(int i){id=i;} public int getDisplayId(){return id;} }\n',
  'android/view/View.java': 'package android.view; public class View { public boolean shown=true, attached=true; public boolean isShown(){return shown;} public boolean isAttachedToWindow(){return attached;} public int getWidth(){return 0;} public int getHeight(){return 0;} }\n',
  'android/view/ViewGroup.java': 'package android.view; public class ViewGroup extends View { public java.util.List<View> kids=new java.util.ArrayList<>(); public int getChildCount(){return kids.size();} public View getChildAt(int i){return kids.get(i);} }\n',
  'android/view/Window.java': 'package android.view; public class Window { public View decor; public View getDecorView(){return decor;} }\n',
  'android/view/WindowManager.java': 'package android.view; public class WindowManager { public Display d; public WindowManager(int id){d=new Display(id);} public Display getDefaultDisplay(){return d;} }\n',
  'com/naver/map/carrot/CarrotNaverBridge.java': 'package com.naver.map.carrot; class CarrotNaverBridge { private static volatile Object activity; static void setActivity(Object a){activity=a;} public int sent; public String lastName; public String lastValue; public android.graphics.Bitmap last;\n void sendBitmap(Object b){sent++;last=(android.graphics.Bitmap)b;}\n private void send(String name,String value){sent++;lastName=name;lastValue=value;} }',
  'com/naver/map/carrot/FakeProvider.java': 'package com.naver.map.carrot; public class FakeProvider { public com.naver.maps.map.NaverMap map; public com.naver.maps.map.NaverMap i(){return map;} }\n',
  'com/naver/map/carrot/SnapCheck.java': 'package com.naver.map.carrot; import android.os.*;\npublic class SnapCheck { static void c(boolean ok,String w){if(!ok)throw new AssertionError(w);}\n public static void main(String[] a){\n  CarrotNaverBridge b=new CarrotNaverBridge(); FakeProvider p=new FakeProvider();\n  c(!CarrotCarMapSnapshot.capture(b),"no provider, no activity -> phone capture");\n  // nMirror case: two MainActivity instances. Newest resumed (phone display 0) has a hidden map,\n  // the older one on the virtual display 2 is the one really showing.\n  class NaviMapView extends com.naver.maps.map.MapView {}\n  android.app.Activity car=new android.app.Activity(2); { android.view.ViewGroup d=new android.view.ViewGroup(); android.view.ViewGroup mid=new android.view.ViewGroup(); NaviMapView mv=new NaviMapView(); mv.a0.map=new com.naver.maps.map.NaverMap(); mid.kids.add(new android.view.View()); mid.kids.add(mv); d.kids.add(mid); car.w.decor=d; }\n  android.app.Activity phone=new android.app.Activity(0); NaviMapView hidden=new NaviMapView(); hidden.shown=false; hidden.a0.map=new com.naver.maps.map.NaverMap(); { android.view.ViewGroup d=new android.view.ViewGroup(); d.kids.add(hidden); phone.w.decor=d; }\n  CarrotCarMapSnapshot.registerActivity(car); CarrotCarMapSnapshot.registerActivity(phone); CarrotNaverBridge.setActivity(phone);\n  c(CarrotCarMapSnapshot.capture(b),"phone MapView map -> owns map_main"); Handler.drain();\n  com.naver.maps.map.NaverMap carMap=((NaviMapView)((android.view.ViewGroup)((android.view.ViewGroup)car.w.decor).kids.get(0)).kids.get(1)).a0.map;\n  c(carMap.requests==1 && hidden.a0.map.requests==0,"snapshot taken from the SHOWING (car display) map, not the hidden phone copy");\n  carMap.cb.a(new android.graphics.Bitmap(1080,2220)); Handler.drain();\n  android.graphics.Rect ps=android.graphics.Canvas.lastSrc; c(ps.left==0 && ps.right==1080 && (ps.bottom-ps.top)==648 && ps.top>1000 && ps.top<1100,"portrait band biased low "+ps.top);\n  c(b.sent==1,"phone snapshot sent");\n  car.fin=true; phone.fin=true; CarrotNaverBridge.setActivity(null); b.sent=0; SystemClock.now+=20000;\n  c(!CarrotCarMapSnapshot.capture(b),"activity gone -> release");\n  CarrotCarMapSnapshot.provider(p);\n  c(!CarrotCarMapSnapshot.capture(b),"map not ready -> phone capture");\n  p.map=new com.naver.maps.map.NaverMap();\n  c(CarrotCarMapSnapshot.capture(b),"map ready -> owns map_main"); Handler.drain();\n  c(p.map.requests==1,"snapshot requested");\n  SystemClock.now+=500; CarrotCarMapSnapshot.capture(b); Handler.drain(); c(p.map.requests==1,"no overlapping request while pending");\n  p.map.cb.a(new android.graphics.Bitmap(1024,600)); Handler.drain();\n  c(b.sent==1 && "map_main".equals(b.lastName) && b.lastValue.contains("\\"width\\":640") && b.lastValue.contains("\\"data\\":\\""),"map_main JSON at 640x384 via bridge.send");\n  android.graphics.Rect s=android.graphics.Canvas.lastSrc; c(s.top==0 && s.bottom==600 && s.left==12 && s.right==1012,"center crop 1024x600 -> 1000x600 "+s.left+","+s.right);\n  SystemClock.now+=500; c(CarrotCarMapSnapshot.capture(b),"next tick"); Handler.drain(); c(p.map.requests==2,"second request after callback");\n  SystemClock.now+=3500; CarrotCarMapSnapshot.capture(b); Handler.drain(); c(p.map.requests==3,"timeout re-request");\n  SystemClock.now+=13000; c(!CarrotCarMapSnapshot.capture(b),"dead renderer -> release map_main");\n  p.map.cb.a(new android.graphics.Bitmap(800,480)); Handler.drain(); SystemClock.now+=500; c(CarrotCarMapSnapshot.capture(b),"recovers after a bitmap");\n  System.out.println("PASS snapshot path");\n } }\n',
  'com/naver/maps/map/MapView.java': 'package com.naver.maps.map; public class MapView extends android.view.ViewGroup { public MapViewDelegate a0=new MapViewDelegate(); }\n',
  'com/naver/maps/map/MapViewDelegate.java': 'package com.naver.maps.map; public class MapViewDelegate { public NaverMap map; public NaverMap f(){return map;} }\n',
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
