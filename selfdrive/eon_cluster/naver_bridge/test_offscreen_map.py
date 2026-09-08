"""Run the HUD8 offscreen map renderer against a fake Android API and a fake Naver SDK.

The fake SDK reproduces the R8-obfuscated member names of CarrotNaver 6.9.1.3
(MapSurface.h/n/l/r/q/f, NaverMap.A1/D1/b2/m0/Y0, CameraUpdate.x/b, Overlay.o, ...)
so the production reflection paths are exercised end to end: lifecycle order,
camera follow, route overlay + progress, frame throttling, stall restart and the
final fallback.  It does not validate GPU rendering or a real S9.

Usage: python test_offscreen_map.py --java-home /path/to/jdk --work /new/scratch/dir
"""
import argparse
from pathlib import Path
import subprocess

from build_offscreen_map import patch_bridge


def check_bridge_fallback_order():
  source = """.method private captureMap()V
    .locals 1

    invoke-static {p0}, Lcom/naver/map/carrot/CarrotCarMapCapture;->capture(Lcom/naver/map/carrot/CarrotNaverBridge;)Z

    move-result v0

    if-eqz v0, :cond_0

    return-void

    :cond_0
    return-void
.end method
"""
  patched = patch_bridge(source)
  assert "CarrotOffscreenMap;->capture" in patched
  assert "CarrotCarMapCapture;->capture" not in patched
  assert ":cond_0" in patched


SOURCES = {
  "android/content/Context.java": """package android.content;
public class Context { public Context getApplicationContext(){return this;} }""",
  "android/app/ActivityThread.java": """package android.app;
public class ActivityThread { public static android.content.Context currentApplication(){return new android.content.Context();} }""",
  "android/os/Bundle.java": """package android.os;
public class Bundle {}""",
  "android/os/Looper.java": """package android.os;
public class Looper { static final Looper MAIN=new Looper(); public static Looper getMainLooper(){return MAIN;} }""",
  "android/os/Handler.java": """package android.os;
public class Handler {
 public static java.util.List<Runnable> queue=new java.util.ArrayList<>();
 public Handler(Looper l){}
 public boolean post(Runnable r){queue.add(r);return true;}
 public static void drain(){ while(!queue.isEmpty()){ Runnable r=queue.remove(0); r.run(); } }
}""",
  "android/os/HandlerThread.java": """package android.os;
public class HandlerThread { public HandlerThread(String n){} public void start(){} public Looper getLooper(){return Looper.getMainLooper();} }""",
  "android/os/SystemClock.java": """package android.os;
public class SystemClock { public static long now=1000; public static long elapsedRealtime(){return now;} }""",
  "android/util/Log.java": """package android.util;
public class Log { public static int i(String t,String m){System.out.println(t+": "+m);return 0;} public static int w(String t,String m){System.out.println(t+" W: "+m);return 0;} public static int e(String t,String m){System.out.println(t+" E: "+m);return 0;} }""",
  "android/view/Surface.java": """package android.view;
public class Surface { public boolean isValid(){return true;} }""",
  "android/graphics/PixelFormat.java": """package android.graphics;
public class PixelFormat { public static final int RGBA_8888=1; }""",
  "android/graphics/Bitmap.java": """package android.graphics;
public class Bitmap {
 public enum Config { ARGB_8888 }
 public final int w,h; boolean recycled; public byte[] pixels;
 Bitmap(int w,int h){this.w=w;this.h=h;}
 public static Bitmap createBitmap(int w,int h,Config c){return new Bitmap(w,h);}
 public static Bitmap createBitmap(Bitmap src,int x,int y,int w,int h){Bitmap b=new Bitmap(w,h);b.pixels=src.pixels;return b;}
 public void copyPixelsFromBuffer(java.nio.Buffer b){pixels=new byte[b.remaining()];((java.nio.ByteBuffer)b).get(pixels);}
 public int getWidth(){return w;} public int getHeight(){return h;}
 public void recycle(){recycled=true;} public boolean isRecycled(){return recycled;}
}""",
  "android/media/Image.java": """package android.media;
public class Image {
 public static class Plane { java.nio.ByteBuffer buf; int ps,rs; public java.nio.ByteBuffer getBuffer(){return buf;} public int getPixelStride(){return ps;} public int getRowStride(){return rs;} }
 public int w,h,rowStride; public boolean closed;
 public Image(int w,int h,int rowStride){this.w=w;this.h=h;this.rowStride=rowStride;}
 public Plane[] getPlanes(){Plane p=new Plane();p.ps=4;p.rs=rowStride;p.buf=java.nio.ByteBuffer.allocate(rowStride*h);return new Plane[]{p};}
 public int getWidth(){return w;} public int getHeight(){return h;} public void close(){closed=true;}
}""",
  "android/media/ImageReader.java": """package android.media;
public class ImageReader {
 public interface OnImageAvailableListener { void onImageAvailable(ImageReader r); }
 public static ImageReader last; public static int created;
 public final int w,h; public OnImageAvailableListener listener; public Image pending; public boolean closed;
 ImageReader(int w,int h){this.w=w;this.h=h;}
 public static ImageReader newInstance(int w,int h,int f,int n){created++;return last=new ImageReader(w,h);}
 public void setOnImageAvailableListener(OnImageAvailableListener l,android.os.Handler hd){listener=l;}
 public android.view.Surface getSurface(){return new android.view.Surface();}
 public Image acquireLatestImage(){Image i=pending;pending=null;return i;}
 public void close(){closed=true;}
 public void deliver(Image i){pending=i;listener.onImageAvailable(this);}
}""",
  # ---- fake Naver SDK with CarrotNaver 6.9.1.3 obfuscated names ----
  "com/naver/maps/geometry/LatLng.java": """package com.naver.maps.geometry;
public class LatLng { public final double latitude,longitude; public LatLng(double a,double b){latitude=a;longitude=b;} }""",
  "com/naver/maps/map/NaverMapOptions.java": """package com.naver.maps.map;
public class NaverMapOptions { public NaverMap.MapType mapType; public float symbolScale=1f;
 public NaverMapOptions U0(NaverMap.MapType t){mapType=t;return this;} public NaverMapOptions q1(float f){symbolScale=f;return this;} }""",
  "com/naver/maps/map/CameraAnimation.java": """package com.naver.maps.map;
public enum CameraAnimation { None, Linear, Easing, Fly }""",
  "com/naver/maps/map/CameraPosition.java": """package com.naver.maps.map;
public final class CameraPosition { public final com.naver.maps.geometry.LatLng target; public final double zoom,tilt,bearing;
 public CameraPosition(com.naver.maps.geometry.LatLng t,double z){this(t,z,0,0);}
 public CameraPosition(com.naver.maps.geometry.LatLng t,double z,double ti,double b){target=t;zoom=z;tilt=ti;bearing=b;} }""",
  "com/naver/maps/map/CameraUpdate.java": """package com.naver.maps.map;
public class CameraUpdate { public CameraPosition pos; public CameraAnimation anim; public long duration;
 public static CameraUpdate x(CameraPosition p){CameraUpdate u=new CameraUpdate();u.pos=p;return u;}
 public CameraUpdate b(CameraAnimation a,long d){anim=a;duration=d;return this;} }""",
  "com/naver/maps/map/OnMapReadyCallback.java": """package com.naver.maps.map;
public interface OnMapReadyCallback { void s(NaverMap map); }""",
  "com/naver/maps/map/overlay/Overlay.java": """package com.naver.maps.map.overlay;
public abstract class Overlay { public com.naver.maps.map.NaverMap map; public boolean visible;
 public void o(com.naver.maps.map.NaverMap m){map=m;} public void setVisible(boolean v){visible=v;} public void setGlobalZIndex(int z){} }""",
  "com/naver/maps/map/overlay/PathOverlay.java": """package com.naver.maps.map.overlay;
public class PathOverlay extends Overlay { public java.util.List<com.naver.maps.geometry.LatLng> coords; public double progress=-2; public int width,color;
 public PathOverlay(){}
 public void setCoords(java.util.List<com.naver.maps.geometry.LatLng> c){if(c.size()<2)throw new IllegalArgumentException();coords=c;}
 public void setProgress(double p){progress=p;} public void setWidth(int w){width=w;} public void setColor(int c){color=c;}
 public void setOutlineWidth(int w){} public void setOutlineColor(int c){} public void setPassedColor(int c){} public void setPassedOutlineColor(int c){}
 public void setHideCollidedSymbols(boolean z){} }""",
  "com/naver/maps/map/overlay/LocationOverlay.java": """package com.naver.maps.map.overlay;
public class LocationOverlay extends Overlay { public com.naver.maps.geometry.LatLng position; public float bearing;
 public void setPosition(com.naver.maps.geometry.LatLng p){position=p;} public void setBearing(float b){bearing=b;}
 public void setIconWidth(int w){} public void setIconHeight(int h){} }""",
  "com/naver/maps/map/NaverMap.java": """package com.naver.maps.map;
public class NaverMap { public enum MapType { Basic, Navi, Satellite }
 public float buildingHeight=1f; public boolean night=true; public int fpsLimit; public int[] padding; public CameraUpdate lastUpdate; public int moves;
 public com.naver.maps.map.overlay.LocationOverlay lo=new com.naver.maps.map.overlay.LocationOverlay();
 public void A1(float f){buildingHeight=f;} public void D1(int a,int b,int c,int d){padding=new int[]{a,b,c,d};} public void b2(boolean z){night=z;}
 public void N1(int n){fpsLimit=n;}
 public com.naver.maps.map.overlay.LocationOverlay m0(){return lo;} public void Y0(CameraUpdate u){lastUpdate=u;moves++;}
 public void C1(CameraPosition p){lastUpdate=CameraUpdate.x(p);moves++;} }""",
  "com/naver/maps/map/MapSurface.java": """package com.naver.maps.map;
public class MapSurface { public static java.util.List<String> log=new java.util.ArrayList<>(); public static MapSurface last; public static boolean failCreate;
 public final NaverMapOptions options; public NaverMap map=new NaverMap(); public OnMapReadyCallback cb; public static boolean deferReady;
 public MapSurface(android.content.Context c,NaverMapOptions o){ if(failCreate)throw new IllegalStateException("no client"); options=o; last=this; log.add("<init>"); }
 public void h(android.os.Bundle b){log.add("onCreate");} public void n(){log.add("onStart");} public void l(){log.add("onResume");}
 public void k(){log.add("onPause");} public void o(){log.add("onStop");} public void i(){log.add("onDestroy");}
 public void r(android.view.Surface s){log.add("surfaceCreated");} public void q(android.view.Surface s,int w,int hh){log.add("surfaceChanged:"+w+"x"+hh);}
 public void s(){log.add("surfaceDestroyed");}
 public void f(OnMapReadyCallback c){log.add("getMapAsync"); cb=c; if(!deferReady) c.s(map);} }""",
  # ---- fake navigation store (obfuscated Naver app accessors used by the bridge) ----
  "com/naver/map/carrot/FakeStore.java": """package com.naver.map.carrot;
public class FakeStore {
 public static class Box { Object v; public Box(Object v){this.v=v;} public Object getValue(){return v;} }
 public static class Loc { public double latitude,longitude; public Loc(double a,double b){latitude=a;longitude=b;} }
 public static class Pos { public Loc loc; public float heading; public float speed; public Loc getLocation(){return loc;} public float getHeading(){return heading;} public float getSpeedKmPerHour(){return speed;} }
 public static class Path { public java.util.List<Loc> pts; public java.util.List<Loc> getPathPoints(){return pts;} }
 public static class RouteE { public Path p; public Path h(){return p;} }
 public static class RouteK { public RouteE e; public RouteE e(){return e;} }
 public Pos pos=new Pos(); public RouteK route=new RouteK();
 public Box P(){return new Box(pos);} public Box K(){return new Box(route);}
}""",
  "com/naver/map/carrot/CarrotNaverBridge.java": """package com.naver.map.carrot;
class CarrotNaverBridge { private static volatile Object store; public int sent; public android.graphics.Bitmap last; public int cleared;
 static void setStore(Object s){store=s;}
 void sendBitmap(Object b){sent++;last=(android.graphics.Bitmap)b;last.recycle();} void clearMap(){cleared++;} }""",
  "com/naver/map/carrot/OffscreenCheck.java": """package com.naver.map.carrot;
import android.os.*; import android.media.*; import com.naver.maps.map.*;
public class OffscreenCheck {
 static void check(boolean ok,String what){if(!ok)throw new AssertionError(what);}
 public static void main(String[] args) throws Exception {
  CarrotNaverBridge bridge=new CarrotNaverBridge();
  FakeStore store=new FakeStore();
  store.pos.loc=new FakeStore.Loc(37.2500,127.0300); store.pos.heading=90f; store.pos.speed=45f;
  java.util.List<FakeStore.Loc> pts=new java.util.ArrayList<>();
  for(int i=0;i<=100;i++) pts.add(new FakeStore.Loc(37.25, 127.03+i*0.001)); // 100 x ~88 m eastwards
  FakeStore.Path path=new FakeStore.Path(); path.pts=pts; store.route.e=new FakeStore.RouteE(); store.route.e.p=path;
  CarrotNaverBridge.setStore(store);

  // 1. Startup clears stale pixels but keeps HUD7 fallback active until a real frame arrives.
  check(!CarrotOffscreenMap.capture(bridge),"first poll keeps fallback active");
  check(bridge.cleared==1,"startup clears previous app map");
  Handler.drain();
  check(MapSurface.log.equals(java.util.Arrays.asList("<init>","onCreate","getMapAsync","surfaceCreated","surfaceChanged:960x576","onStart","onResume")),"lifecycle order "+MapSurface.log);
  check(MapSurface.last.options.mapType==NaverMap.MapType.Navi,"navi map type via options");
  NaverMap map=MapSurface.last.map;
  check(map.buildingHeight==0f && !map.night && map.fpsLimit==12 && map.padding[1]==288,"map setup: buildings off, day, fps limit, top padding");
  check(map.moves>=1 && map.lastUpdate.pos.bearing==90.0 && map.lastUpdate.anim==CameraAnimation.Linear,"camera follows heading");
  check(map.lo.position.latitude==37.25 && map.lo.bearing==90f && map.lo.visible,"location overlay follows");

  // 2. Route drawn from getPathPoints; progress starts near 0 and follows the vehicle.
  SystemClock.now+=300; check(!CarrotOffscreenMap.capture(bridge),"fallback remains before first frame"); Handler.drain();
  com.naver.maps.map.overlay.PathOverlay po=null;
  // Locate the path overlay through the map reference it was attached to.
  java.lang.reflect.Field f=CarrotOffscreenMap.class.getDeclaredField("pathOverlay"); f.setAccessible(true); po=(com.naver.maps.map.overlay.PathOverlay)f.get(null);
  check(po!=null && po.map==map && po.coords.size()==101,"path overlay attached with 101 coords");
  check(po.progress>=0.0 && po.progress<0.02,"progress at start "+po.progress);
  store.pos.loc=new FakeStore.Loc(37.25,127.03+0.050); store.pos.speed=100f; store.pos.heading=95f;
  SystemClock.now+=300; CarrotOffscreenMap.capture(bridge); Handler.drain();
  check(Math.abs(po.progress-0.5)<0.03,"progress mid route "+po.progress);
  check(map.lastUpdate.pos.zoom<16.0 && map.lastUpdate.pos.zoom>15.0,"zoom widens with speed "+map.lastUpdate.pos.zoom);
  // Below 3 km/h the last bearing is kept (GPS heading noise while stopped).
  store.pos.speed=0f; store.pos.heading=270f; SystemClock.now+=300; CarrotOffscreenMap.capture(bridge); Handler.drain();
  check(map.lastUpdate.pos.bearing==95.0,"stopped keeps bearing "+map.lastUpdate.pos.bearing);

  // 3. Frames: row padding cropped, throttled to 5 fps, delivered through bridge.sendBitmap.
  ImageReader reader=ImageReader.last;
  reader.deliver(new Image(960,576,960*4+64));
  check(bridge.sent==1 && bridge.last.getWidth()==960 && bridge.last.getHeight()==576,"first frame sent 960x576");
  check(CarrotOffscreenMap.capture(bridge) && CarrotOffscreenMap.active(),"first frame takes map ownership");
  reader.deliver(new Image(960,576,960*4));
  check(bridge.sent==1,"second frame within 200 ms dropped");
  SystemClock.now+=250; reader.deliver(new Image(960,576,960*4));
  check(bridge.sent==2,"frame after 200 ms sent");

  // 4. Route cleared when navigation ends.
  store.route.e.p.pts=new java.util.ArrayList<>();
  SystemClock.now+=3100; CarrotOffscreenMap.capture(bridge); Handler.drain();
  check(po.map==null,"path overlay detached without route");

  // 5. Stall: no frames for 20 s -> renderer restarted (new ImageReader), still owning map_main.
  int readers=ImageReader.created;
  SystemClock.now+=21000; check(CarrotOffscreenMap.capture(bridge),"still owns during restart"); Handler.drain();
  check(ImageReader.created==readers+1 && reader.closed,"restart created a new reader");
  check(MapSurface.log.contains("surfaceDestroyed") && MapSurface.log.contains("onDestroy"),"old surface torn down");
  // Frames after restart keep flowing.
  SystemClock.now+=300; ImageReader.last.deliver(new Image(960,576,960*4)); check(bridge.sent==3,"frame after restart");

  // 6. Repeated stalls give up and hand map_main back to the HUD7 capture paths.
  for(int i=0;i<4;i++){ SystemClock.now+=21000; CarrotOffscreenMap.capture(bridge); Handler.drain(); }
  check(!CarrotOffscreenMap.capture(bridge) && !CarrotOffscreenMap.active(),"fallback after repeated stalls");
  System.out.println("PASS: lifecycle, navi options, camera follow, route+progress, frame throttle/crop, stall restart, final fallback");
 }
}""",
  "com/naver/map/carrot/OffscreenInitFailCheck.java": """package com.naver.map.carrot;
import android.os.*; import com.naver.maps.map.*;
public class OffscreenInitFailCheck {
 public static void main(String[] args) throws Exception {
  MapSurface.failCreate=true;
  CarrotNaverBridge bridge=new CarrotNaverBridge();
  if(CarrotOffscreenMap.capture(bridge)) throw new AssertionError("startup must keep fallback active");
  Handler.drain();
  if(CarrotOffscreenMap.capture(bridge)) throw new AssertionError("init failure must fall back");
  System.out.println("PASS: SDK construction failure falls back to HUD7 capture");
 }
}""",
}


def main():
  check_bridge_fallback_order()
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--java-home", type=Path, required=True)
  parser.add_argument("--work", type=Path, required=True)
  parser.add_argument("--source", type=Path, default=Path(__file__).with_name("CarrotOffscreenMap.java"))
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
  files = [str(p) for p in src.rglob("*.java")] + [str(args.source)]
  subprocess.run([str(args.java_home / ("bin/javac" + suffix)), "--release", "8", "-encoding", "UTF-8",
                  "-Xlint:-options", "-d", str(classes)] + files, check=True)
  for main_class in ("com.naver.map.carrot.OffscreenCheck", "com.naver.map.carrot.OffscreenInitFailCheck"):
    subprocess.run([str(args.java_home / ("bin/java" + suffix)), "-cp", str(classes), main_class], check=True)


if __name__ == "__main__":
  main()
