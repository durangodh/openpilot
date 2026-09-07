"""Run capture routing/lifetime regression tests with a small fake Android API.

This checks the production Java class, not actual GPU pixels or device rendering.
Usage: python test_capture.py --java-home /path/to/jdk --work /new/scratch/dir
"""
import argparse
from pathlib import Path
import subprocess


SOURCES = {
  "android/view/ViewParent.java": """package android.view;
public interface ViewParent { ViewParent getParent(); }""",
  "android/view/View.java": """package android.view;
public class View implements ViewParent {
 public boolean shown=true; public ViewParent parent;
 public ViewParent getParent(){return parent;} public boolean isShown(){return shown;}
 public boolean isAttachedToWindow(){return shown;} public float getAlpha(){return 1;}
 public int getWidth(){return 640;} public int getHeight(){return 384;}
 public boolean getGlobalVisibleRect(android.graphics.Rect r){return shown;}
}""",
  "android/view/ViewGroup.java": """package android.view;
public class ViewGroup extends View {
 public java.util.List<View> children=new java.util.ArrayList<>();
 public void add(View v){children.add(v);v.parent=this;}
 public int getChildCount(){return children.size();} public View getChildAt(int i){return children.get(i);}
}""",
  "android/view/TextureView.java": """package android.view;
public class TextureView extends View {
 public boolean available=true, fail=false;
 public boolean isAvailable(){return available;}
 public android.graphics.Bitmap getBitmap(android.graphics.Bitmap b){return fail?null:b;}
}""",
  "android/view/SurfaceView.java": """package android.view;
public class SurfaceView extends View {
 public Holder getHolder(){return new Holder();}
 public static class Holder { public Surface getSurface(){return new Surface();} public android.graphics.Rect getSurfaceFrame(){return new android.graphics.Rect();} }
 public static class Surface { public boolean isValid(){return true;} }
}""",
  "android/view/PixelCopy.java": """package android.view;
public class PixelCopy {
 public static final int SUCCESS=0; public static int calls=0, result=0;
 public interface OnPixelCopyFinishedListener { void onPixelCopyFinished(int result); }
 public static void request(SurfaceView s, android.graphics.Bitmap b,
  OnPixelCopyFinishedListener cb, android.os.Handler h){calls++;cb.onPixelCopyFinished(result);}
 // Deliberately no Window overload: production must never fall back to it.
}""",
  "android/view/Window.java": """package android.view;
public class Window { public ViewGroup root=new ViewGroup(); public View getDecorView(){return root;} }""",
  "android/app/Activity.java": """package android.app;
public class Activity {
 public android.view.Window window=new android.view.Window();
 public boolean isFinishing(){return false;} public boolean isDestroyed(){return false;}
 public android.view.Window getWindow(){return window;}
}""",
  "android/graphics/Rect.java": """package android.graphics;
public class Rect { int w=640,h=384; public Rect(){} public Rect(int l,int t,int r,int b){w=r-l;h=b-t;} public int width(){return w;} public int height(){return h;} }""",
  "android/graphics/Bitmap.java": """package android.graphics;
public class Bitmap {
 public enum Config { ARGB_8888 } public boolean recycled=false; int width,height; public int getWidth(){return width;} public int getHeight(){return height;}
 public static Bitmap createBitmap(int w,int h,Config c){Bitmap b=new Bitmap();b.width=w;b.height=h;return b;}
 public void recycle(){recycled=true;} public boolean isRecycled(){return recycled;}
}""",
  "android/graphics/Paint.java": """package android.graphics;
public class Paint { public static final int FILTER_BITMAP_FLAG=2; public Paint(int flags){} }""",
  "android/graphics/Canvas.java": """package android.graphics;
public class Canvas { public Canvas(Bitmap b){} public void drawBitmap(Bitmap b,Rect src,Rect dst,Paint p){
 double x=dst.width()/(double)src.width(), y=dst.height()/(double)src.height();
 if(Math.abs(x-y)>0.01)throw new AssertionError("distorted bitmap");
} }""",
  "android/os/Handler.java": """package android.os;
public class Handler { public Handler(Object looper){} public boolean post(Runnable r){r.run();return true;} }""",
  "android/os/HandlerThread.java": """package android.os;
public class HandlerThread { public HandlerThread(String n){} public void start(){} public Object getLooper(){return null;} }""",
  "android/os/SystemClock.java": """package android.os;
public class SystemClock { static long time=0; public static long elapsedRealtime(){return time+=2000;} }""",
  "com/naver/maps/map/MapView.java": """package com.naver.maps.map;
public class MapView extends android.view.ViewGroup {}""",
  "com/navercorp/android/vgx/lib/VgxGLTextureView.java": """package com.navercorp.android.vgx.lib;
public class VgxGLTextureView extends android.view.TextureView {}""",
  "com/naver/map/carrot/CarrotNaverBridge.java": """package com.naver.map.carrot;
public class CarrotNaverBridge {
 public int sent=0,cleared=0; public android.graphics.Bitmap last;
 void sendBitmap(Object b){sent++;last=(android.graphics.Bitmap)b;last.recycle();}
 void clearMap(){cleared++;}
}""",
  "com/naver/map/carrot/CaptureCheck.java": """package com.naver.map.carrot;
import android.app.Activity;
import android.view.*;
public class CaptureCheck {
 static void check(boolean ok){if(!ok)throw new AssertionError();}
 public static void main(String[] args){
  for(int[] wh:new int[][]{{1080,2400},{1440,2960},{1920,1080},{640,384},{384,640},{960,576}}){
   int[] size=MapCaptureGeometry.captureSize(wh[0],wh[1]);
   check(size[0]<=2048 && size[1]<=2048);
   check(Math.abs(size[0]/(double)size[1]-wh[0]/(double)wh[1])<0.003);
   int[] c=MapCaptureGeometry.crop(size[0],size[1]);
   check(c[0]>=0 && c[1]>=0 && c[2]<=size[0] && c[3]<=size[1]);
   check(Math.abs((c[2]-c[0])/(double)(c[3]-c[1])-960d/576d)<0.005);
  }
  Activity a=new Activity(); CarrotNaverBridge b=new CarrotNaverBridge();
  CarrotMapCapture.capture(a,b); check(b.sent==0 && b.cleared==1);
  a.window.root.add(new SurfaceView()); a.window.root.add(new TextureView());
  CarrotMapCapture.capture(a,b); check(b.sent==0 && b.cleared==2 && PixelCopy.calls==0);
  com.naver.maps.map.MapView map=new com.naver.maps.map.MapView();
  TextureView texture=new TextureView();map.add(texture);a.window.root.add(map);
  CarrotMapCapture.capture(a,b);check(b.sent==1 && b.last.isRecycled() && PixelCopy.calls==0);
  texture.available=false;CarrotMapCapture.capture(a,b);check(b.sent==1 && b.cleared==3);
  map.children.clear();SurfaceView surface=new SurfaceView();map.add(surface);
  CarrotMapCapture.capture(a,b);check(b.sent==2 && PixelCopy.calls==1 && b.last.isRecycled());
  PixelCopy.result=3;CarrotMapCapture.capture(a,b);check(b.sent==2 && b.cleared==4);
  PixelCopy.result=0;CarrotMapCapture.capture(a,b);check(b.sent==3);
  surface.shown=false;CarrotMapCapture.capture(a,b);check(b.sent==3 && b.cleared==5);
  map.shown=false;CarrotMapCapture.capture(a,b);check(b.sent==3 && b.cleared==6);
  a.window.root.children.clear();
  a.window.root.add(new com.navercorp.android.vgx.lib.VgxGLTextureView());
  CarrotMapCapture.capture(a,b);check(b.sent==4 && b.last.isRecycled());check(b.last.getWidth()==960 && b.last.getHeight()==576);
  System.out.println("PASS: no-map, ads, texture, surface, unavailable/hidden, error recovery, VGX, recycling, portrait/landscape aspect ratio, output dimensions");
 }
}""",
}


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--java-home", type=Path, required=True)
  parser.add_argument("--work", type=Path, required=True)
  args = parser.parse_args()
  args.work.mkdir(parents=True, exist_ok=False)
  files = []
  for name, source in SOURCES.items():
    path = args.work / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    files.append(str(path))
  suffix = ".exe" if (args.java_home / "bin/java.exe").exists() else ""
  classes = args.work / "classes"
  classes.mkdir()
  subprocess.run([str(args.java_home / ("bin/javac" + suffix)), "--release", "8", "-d", str(classes),
                  *files, str(Path(__file__).with_name("CarrotMapCapture.java")),
                  str(Path(__file__).with_name("MapCaptureGeometry.java"))], check=True)
  subprocess.run([str(args.java_home / ("bin/java" + suffix)), "-cp", str(classes),
                  "com.naver.map.carrot.CaptureCheck"], check=True)


if __name__ == "__main__":
  main()
